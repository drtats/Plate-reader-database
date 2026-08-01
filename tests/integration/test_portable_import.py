from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

import plate_reader.infrastructure.database.portable as portable_module
from plate_reader.application.contracts import (
    Actor,
    ImportGrowthRun,
    ImportPortableRun,
    Role,
    UserId,
)
from plate_reader.application.ports import PortableImportPreviewData, PortableImportResultData
from plate_reader.application.services import (
    ImportGrowthRunService,
    ImportPortableRunService,
    PreviewPortableRunService,
    SourceHashMismatchError,
)
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlitePortableRunImporter,
    SqlPlateReaderRepository,
    connect_database,
    export_portable_runs,
    import_portable_file,
    preview_portable_import,
)
from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.infrastructure.database.portable import PortableValidationError

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
GROWTH_CSV = (ROOT / "tests" / "fixtures" / "growth" / "with_time.csv").read_text(encoding="utf-8")


@pytest.fixture
def portable_file(tmp_path: Path) -> Iterator[tuple[Path, str, str]]:
    connection = connect_database(
        DatabaseConfig(tmp_path / "source.sqlite", DatabaseBackend.FAKE_CLOUD, MIGRATIONS)
    )
    repository = SqlPlateReaderRepository(connection)
    plate_id = seed_source(repository)
    revision_id = seed_revision(repository, plate_id)
    path = tmp_path / "portable.sqlite"
    export_portable_runs(
        connection,
        path,
        MIGRATIONS,
        [plate_id],
        revision_ids=[revision_id],
        exporter_version="test/1",
        id_factory=lambda: "export-import-test",
        exported_at="2026-01-05T00:00:00+00:00",
    )
    connection.close()
    yield path, plate_id, revision_id


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def destination(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[Connection, SqlPlateReaderRepository]]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"destination-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(importer_values())
    try:
        yield connection, repository
    finally:
        connection.close()


def test_portable_import_round_trip_and_same_file_idempotency(
    portable_file: tuple[Path, str, str],
    destination: tuple[Connection, SqlPlateReaderRepository],
) -> None:
    path, plate_id, revision_id = portable_file
    connection, repository = destination
    before = preview_portable_import(connection, path)
    assert not any(before.collisions.values())
    first = import_portable_file(
        connection,
        path,
        actor_id="importer-user",
        id_factory=id_sequence(),
        imported_at="2026-01-06T00:00:00+00:00",
    )
    counts_after_first = application_counts(connection)
    second = import_portable_file(
        connection,
        path,
        actor_id="importer-user",
        id_factory=id_sequence(),
        imported_at="2026-01-06T00:00:00+00:00",
    )
    assert first.created is True
    assert second.created is False
    assert second.plate_id_map == first.plate_id_map == {plate_id: plate_id}
    assert second.revision_id_map == first.revision_id_map == {revision_id: revision_id}
    assert application_counts(connection) == counts_after_first
    assert counts_after_first["growth_measurements"] == 384
    assert counts_after_first["wells"] == 96
    assert counts_after_first["analysis_revisions"] == 1
    assert counts_after_first["growth_backgrounds"] == 1
    assert counts_after_first["import_sources"] == 2
    assert counts_after_first["provenance_events"] == 2
    imported_user = repository.user_by_email("editor@example.invalid")
    assert imported_user is not None
    assert imported_user["role"] == "viewer"
    assert imported_user["is_active"] == 0
    snapshot = repository.load_plate(first.plate_id_map[plate_id])
    assert snapshot is not None
    assert len(snapshot.raw_observations) == 384


def test_collision_policy_remaps_without_overwriting(
    portable_file: tuple[Path, str, str],
    destination: tuple[Connection, SqlPlateReaderRepository],
) -> None:
    path, plate_id, _revision_id = portable_file
    connection, repository = destination
    source_preview = preview_portable_import(connection, path).source
    source_sql = sqlite3.connect(path)
    experiment_id = str(
        source_sql.execute(
            "SELECT experiment_id FROM plates WHERE plate_id = ?", (plate_id,)
        ).fetchone()[0]
    )
    source_sql.close()
    with repository.transaction():
        repository.create_experiment(
            {
                "experiment_id": experiment_id,
                "name": "Existing collision",
                "experiment_date": "2025-01-01",
                "created_by": "importer-user",
            }
        )
        repository.create_plate(
            {
                "plate_id": plate_id,
                "experiment_id": experiment_id,
                "assay_type": "growth",
                "plate_name": "Existing plate",
                "created_by": "importer-user",
            }
        )
    preview = preview_portable_import(connection, path)
    assert preview.source.export_id == source_preview.export_id
    assert preview.collisions["experiments"] == 1
    assert preview.collisions["plates"] == 1
    with pytest.raises(PortableValidationError, match="identifier collisions"):
        import_portable_file(
            connection,
            path,
            actor_id="importer-user",
            collision_policy="error",
        )
    report = import_portable_file(
        connection,
        path,
        actor_id="importer-user",
        collision_policy="remap",
        id_factory=id_sequence(),
    )
    assert report.plate_id_map[plate_id] != plate_id
    assert connection.execute(
        "SELECT plate_name FROM plates WHERE plate_id = ?", (plate_id,)
    ).fetchone() == ("Existing plate",)
    assert connection.execute("SELECT count(*) FROM plates").fetchone() == (2,)


def test_import_authorization_and_collision_policy_are_validated(
    portable_file: tuple[Path, str, str],
    destination: tuple[Connection, SqlPlateReaderRepository],
) -> None:
    path, _plate_id, _revision_id = portable_file
    connection, repository = destination
    with pytest.raises(ValueError, match="collision_policy"):
        import_portable_file(
            connection, path, actor_id="importer-user", collision_policy="overwrite"
        )
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": "viewer-user",
                "email": "viewer@example.invalid",
                "display_name": "Viewer",
                "role": "viewer",
                "is_active": True,
            }
        )
    with pytest.raises(PermissionError, match="active editor or admin"):
        import_portable_file(connection, path, actor_id="viewer-user")
    with pytest.raises(PermissionError, match="active editor or admin"):
        import_portable_file(connection, path, actor_id="missing-user")


def test_portable_application_services_preview_commit_and_idempotency(
    portable_file: tuple[Path, str, str],
    destination: tuple[Connection, SqlPlateReaderRepository],
) -> None:
    path, plate_id, _revision_id = portable_file
    connection, repository = destination
    content = path.read_bytes()
    actor = Actor(UserId("importer-user"), "importer@example.invalid", Role.EDITOR)
    adapter = SqlitePortableRunImporter(connection)
    preview = PreviewPortableRunService(repository, adapter).execute(actor, content)
    assert isinstance(preview, PortableImportPreviewData)
    assert preview.plate_ids == (plate_id,)

    service = ImportPortableRunService(repository, adapter)
    dry_run = service.execute(
        ImportPortableRun(actor, hashlib.sha256(content).hexdigest()), content
    )
    assert isinstance(dry_run, PortableImportPreviewData)
    committed = service.execute(
        ImportPortableRun(
            actor,
            hashlib.sha256(content).hexdigest(),
            dry_run=False,
        ),
        content,
    )
    repeated = service.execute(
        ImportPortableRun(
            actor,
            hashlib.sha256(content).hexdigest(),
            dry_run=False,
        ),
        content,
    )
    assert isinstance(committed, PortableImportResultData)
    assert isinstance(repeated, PortableImportResultData)
    assert committed.created is True
    assert repeated.created is False
    assert repeated.plate_id_map == committed.plate_id_map
    with pytest.raises(SourceHashMismatchError):
        service.execute(ImportPortableRun(actor, "0" * 64), content)


def test_forced_portable_import_failure_rolls_back(
    portable_file: tuple[Path, str, str],
    destination: tuple[Connection, SqlPlateReaderRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _plate_id, _revision_id = portable_file
    connection, _repository = destination
    original = portable_module._insert_dict_rows

    def fail_on_measurements(
        selected_connection: Connection,
        table: str,
        rows: list[dict[str, object]],
    ) -> None:
        if table == "growth_measurements":
            raise RuntimeError("forced portable failure")
        original(selected_connection, table, rows)

    monkeypatch.setattr(portable_module, "_insert_dict_rows", fail_on_measurements)
    with pytest.raises(RuntimeError, match="forced portable failure"):
        import_portable_file(connection, path, actor_id="importer-user")
    counts = application_counts(connection)
    assert counts["users"] == 1
    assert all(count == 0 for table, count in counts.items() if table != "users")


def seed_source(repository: SqlPlateReaderRepository) -> str:
    service = ImportGrowthRunService(repository, id_factory=source_id_sequence())
    command = ImportGrowthRun(
        actor=Actor(UserId("source-editor"), "editor@example.invalid", Role.ADMIN),
        source_name="source.csv",
        source_sha256=hashlib.sha256(GROWTH_CSV.encode()).hexdigest(),
        parser_version=GROWTH_NORMALIZATION_VERSION,
        experiment_name="Portable source",
        plate_name="Source plate",
        experiment_date=date(2026, 1, 5),
    )
    return str(service.execute(command, GROWTH_CSV).plate_id)


def seed_revision(repository: SqlPlateReaderRepository, plate_id: str) -> str:
    with repository.transaction():
        revision = repository.add_analysis_revision(
            {
                "revision_id": "source-revision",
                "plate_id": plate_id,
                "assay_type": "growth",
                "algorithm_name": "growth_background",
                "algorithm_version": "growth-background/1.0.0",
                "parameters_json": {},
                "input_sha256": "raw-source-hash",
                "created_by": "source-editor",
            }
        )
        repository.insert_growth_backgrounds(
            revision,
            [
                {
                    "background_group": "plate",
                    "channel": "od600",
                    "time_index": 0,
                    "elapsed_microseconds": 0,
                    "mean_value": 0.05,
                    "std_value": 0.001,
                    "coefficient_of_variation": 0.02,
                    "blank_count": 2,
                    "qc_status": "good",
                }
            ],
        )
    return str(revision)


def importer_values() -> dict[str, object]:
    return {
        "user_id": "importer-user",
        "email": "importer@example.invalid",
        "display_name": "Importer",
        "role": "editor",
        "is_active": True,
    }


def source_id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"source-generated-{next(counter):04d}"


def id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"import-generated-{next(counter):04d}"


def application_counts(connection: Connection) -> dict[str, int]:
    tables = (
        "users",
        "experiments",
        "plates",
        "wells",
        "well_conditions",
        "growth_measurements",
        "analysis_revisions",
        "growth_backgrounds",
        "growth_metrics",
        "mic_readings",
        "mic_well_calls",
        "mic_results",
        "import_sources",
        "provenance_events",
    )
    return {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in tables
    }
