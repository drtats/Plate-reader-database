from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import Actor, ImportGrowthRun, Role, UserId
from plate_reader.application.services import ImportGrowthRunService
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    backup_complete_database,
    connect_database,
    export_portable_runs,
    restore_complete_database,
    restore_complete_database_to_connection,
    validate_portable_file,
)
from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.infrastructure.database.portable import (
    PORTABLE_DATA_TABLES,
    TABLE_COLUMNS,
    PortableValidationError,
    logical_table_hash,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
GROWTH_CSV = (ROOT / "tests" / "fixtures" / "growth" / "with_time.csv").read_text(encoding="utf-8")


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def source(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[Connection, SqlPlateReaderRepository]]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"portable-source-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    try:
        yield connection, SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def test_portable_export_validates_and_opens_with_standard_sqlite(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    plate_id = seed_import(repository)
    revision_id = seed_revision(repository, plate_id)
    destination = tmp_path / "run.plate-reader.sqlite"
    report = export_portable_runs(
        connection,
        destination,
        MIGRATIONS,
        [plate_id],
        revision_ids=[revision_id],
        exporter_version="test-exporter/1",
        id_factory=lambda: "export-1",
        exported_at="2026-01-04T00:00:00+00:00",
    )
    preview = validate_portable_file(destination)
    assert report.export_id == preview.export_id == "export-1"
    assert preview.plate_ids == (plate_id,)
    assert preview.revision_ids == (revision_id,)
    assert report.file_sha256 == preview.file_sha256
    assert report.table_counts == preview.table_counts
    assert report.table_counts["growth_measurements"] == 384
    assert report.table_counts["growth_backgrounds"] == 1

    standard = sqlite3.connect(destination)
    try:
        assert standard.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert standard.execute("SELECT count(*) FROM wells").fetchone() == (96,)
        assert standard.execute("SELECT count(*) FROM portable_manifest").fetchone() == (1,)
    finally:
        standard.close()


def test_portable_checksum_detects_metadata_tampering(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    plate_id = seed_import(repository)
    destination = tmp_path / "tampered.sqlite"
    export_portable_runs(
        connection,
        destination,
        MIGRATIONS,
        [plate_id],
        exporter_version="test/1",
    )
    tampered = sqlite3.connect(destination)
    tampered.execute("UPDATE plates SET plate_name = 'tampered'")
    tampered.commit()
    tampered.close()
    with pytest.raises(PortableValidationError, match="checksum mismatch: plates"):
        validate_portable_file(destination)


def test_portable_rejects_unknown_executable_schema_object(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    plate_id = seed_import(repository)
    destination = tmp_path / "view.sqlite"
    export_portable_runs(
        connection,
        destination,
        MIGRATIONS,
        [plate_id],
        exporter_version="test/1",
    )
    changed = sqlite3.connect(destination)
    changed.execute("CREATE VIEW unsafe_view AS SELECT * FROM users")
    changed.commit()
    changed.close()
    with pytest.raises(PortableValidationError, match="executable schema"):
        validate_portable_file(destination)


def test_portable_selection_errors_do_not_leave_partial_files(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    plate_id = seed_import(repository)
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(PortableValidationError, match="Unknown plate IDs"):
        export_portable_runs(
            connection,
            missing,
            MIGRATIONS,
            ["not-a-plate"],
            exporter_version="test/1",
        )
    assert not missing.exists()
    with pytest.raises(PortableValidationError, match="At least one plate"):
        export_portable_runs(
            connection,
            tmp_path / "empty.sqlite",
            MIGRATIONS,
            [],
            exporter_version="test/1",
        )
    destination = tmp_path / "existing.sqlite"
    destination.touch()
    with pytest.raises(FileExistsError):
        export_portable_runs(
            connection,
            destination,
            MIGRATIONS,
            [plate_id],
            exporter_version="test/1",
        )


def test_complete_backup_preserves_every_application_table(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    seed_import(repository)
    with repository.transaction():
        connection.execute(
            "INSERT INTO saved_options VALUES (?, ?, ?, ?)",
            ("medium", "Synthetic medium", "user-editor", "2026-01-04T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO plate_templates VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "template-1",
                "Synthetic template",
                "growth",
                "{}",
                "user-editor",
                "2026-01-04T00:00:00+00:00",
                "2026-01-04T00:00:00+00:00",
            ),
        )
    destination = tmp_path / "complete-backup.sqlite"
    backup_complete_database(connection, destination, MIGRATIONS)
    backup_sqlite = sqlite3.connect(destination)
    backup = cast_connection(backup_sqlite)
    try:
        assert backup_sqlite.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        for table in TABLE_COLUMNS:
            assert logical_table_hash(connection, table) == logical_table_hash(backup, table)
    finally:
        backup_sqlite.close()
    with pytest.raises(FileExistsError):
        backup_complete_database(connection, destination, MIGRATIONS)


def test_complete_backup_restore_drill_verifies_every_table(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    seed_import(repository)
    backup = tmp_path / "drill-backup.sqlite"
    restored = tmp_path / "drill-restored.sqlite"
    backup_complete_database(connection, backup, MIGRATIONS)

    report = restore_complete_database(backup, restored, MIGRATIONS)

    assert report.path == restored
    assert report.table_counts["growth_measurements"] == 384
    restored_sqlite = sqlite3.connect(restored)
    try:
        restored_connection = cast_connection(restored_sqlite)
        for table in TABLE_COLUMNS:
            assert logical_table_hash(connection, table) == logical_table_hash(
                restored_connection, table
            )
    finally:
        restored_sqlite.close()
    with pytest.raises(FileExistsError):
        restore_complete_database(backup, restored, MIGRATIONS)


def test_complete_backup_restores_transactionally_to_empty_connection(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    seed_import(repository)
    backup = tmp_path / "remote-style-backup.sqlite"
    backup_complete_database(connection, backup, MIGRATIONS)
    target = connect_database(
        DatabaseConfig(tmp_path / "remote-target.sqlite", DatabaseBackend.FAKE_CLOUD, MIGRATIONS)
    )
    try:
        report = restore_complete_database_to_connection(backup, target)
        assert report.table_counts["growth_measurements"] == 384
        for table in TABLE_COLUMNS:
            assert logical_table_hash(connection, table) == logical_table_hash(target, table)
        with pytest.raises(PortableValidationError, match="not empty"):
            restore_complete_database_to_connection(backup, target)
    finally:
        target.close()


def test_complete_restore_rejects_modified_backup_schema(
    source: tuple[Connection, SqlPlateReaderRepository], tmp_path: Path
) -> None:
    connection, repository = source
    seed_import(repository)
    backup = tmp_path / "unsafe-backup.sqlite"
    backup_complete_database(connection, backup, MIGRATIONS)
    changed = sqlite3.connect(backup)
    changed.execute("CREATE VIEW unexpected AS SELECT * FROM users")
    changed.commit()
    changed.close()

    restored = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(PortableValidationError, match="executable schema"):
        restore_complete_database(backup, restored, MIGRATIONS)
    assert not restored.exists()


def test_portable_hash_manifest_covers_every_exported_table() -> None:
    assert set(PORTABLE_DATA_TABLES) < set(TABLE_COLUMNS)


def seed_import(repository: SqlPlateReaderRepository) -> str:
    service = ImportGrowthRunService(repository, id_factory=id_sequence())
    command = ImportGrowthRun(
        actor=Actor(UserId("user-editor"), "editor@example.invalid", Role.EDITOR),
        source_name="synthetic.csv",
        source_sha256=hashlib.sha256(GROWTH_CSV.encode()).hexdigest(),
        parser_version=GROWTH_NORMALIZATION_VERSION,
        experiment_name="Portable synthetic",
        plate_name="Portable plate",
        experiment_date=date(2026, 1, 4),
    )
    return str(service.execute(command, GROWTH_CSV).plate_id)


def seed_revision(repository: SqlPlateReaderRepository, plate_id: str) -> str:
    well_id = str(
        repository.connection.execute(
            "SELECT well_id FROM wells WHERE plate_id = ? AND position = 'A1'", (plate_id,)
        ).fetchone()[0]
    )
    with repository.transaction():
        revision_id = repository.add_analysis_revision(
            {
                "revision_id": "revision-exported",
                "plate_id": plate_id,
                "assay_type": "growth",
                "algorithm_name": "growth_background",
                "algorithm_version": "growth-background/1.0.0",
                "parameters_json": {},
                "input_sha256": "raw-hash",
                "created_by": "user-editor",
            }
        )
        repository.insert_growth_backgrounds(
            revision_id,
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
        repository.insert_growth_metrics(
            revision_id,
            [
                {
                    "well_id": well_id,
                    "channel": "od600",
                    "metric_name": "auc",
                    "metric_value": 1.0,
                }
            ],
        )
    return str(revision_id)


def id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"portable-generated-{next(counter):04d}"


def cast_connection(connection: sqlite3.Connection) -> Connection:
    return connection  # type: ignore[return-value]
