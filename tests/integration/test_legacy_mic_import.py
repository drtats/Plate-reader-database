from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.importers import (
    LegacyMicValidationError,
    import_legacy_mic_file,
    preview_legacy_mic_file,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
LEGACY_FIXTURE = ROOT / "tests" / "fixtures" / "legacy" / "mic_legacy.sqlite"
FIXTURE_SHA256 = "d6effb6b13408266927cacf8cc745f5420da031928a6590017f18f7069834af4"
ACTOR = Actor(UserId("legacy-mic-editor"), "legacy-mic@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"legacy-mic-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": ACTOR.user_id,
                "email": ACTOR.email,
                "display_name": "Legacy MIC Editor",
                "role": ACTOR.role,
                "is_active": True,
            }
        )
    try:
        yield repository
    finally:
        connection.close()


def test_preview_reconciles_legacy_derived_values(tmp_path: Path) -> None:
    source = copied_fixture(tmp_path)

    preview = preview_legacy_mic_file(source)

    assert preview.detected_version == "mic-sqlite-v1-current"
    assert preview.file_sha256 == FIXTURE_SHA256
    assert len(preview.plates) == 1
    plate = preview.plates[0]
    assert plate.plate_id == "synthetic-mic-plate"
    assert (plate.well_count, plate.legacy_result_count, plate.calculated_result_count) == (
        96,
        4,
        4,
    )
    assert plate.raw_sha256 == "d4e5b6b796299ba4e011075e78aff7e2553888de36d9d1901029394e3a135262"
    assert plate.derived_differences == ()
    assert plate.errors == ()
    assert plate.is_checked is True
    assert plate.missing_target_fields == (
        "experiment_name",
        "project",
        "instrument",
        "deletion_actor",
        "deletion_time",
    )
    assert file_sha256(source) == FIXTURE_SHA256


def test_dry_run_writes_nothing(repository: SqlPlateReaderRepository, tmp_path: Path) -> None:
    source = copied_fixture(tmp_path)
    before = counts(repository)

    report = import_legacy_mic_file(source, repository, ACTOR)

    assert report.dry_run is True
    assert report.source_unchanged is True
    assert report.plates[0].status == "ready"
    assert report.plates[0].counts == {"wells": 96, "readings": 96, "calls": 96, "results": 4}
    assert counts(repository) == before
    assert file_sha256(source) == FIXTURE_SHA256


def test_commit_preserves_metadata_state_custom_labels_and_is_idempotent(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute(
            "UPDATE wells SET extra_labels_json = ? WHERE well_position = 'A1'",
            ('{"oxygen":"aerobic","batch":"B7"}',),
        )
    source_hash = file_sha256(source)

    report = import_legacy_mic_file(
        source,
        repository,
        ACTOR,
        dry_run=False,
        id_factory=id_sequence(),
        now_factory=lambda: "2026-08-01T12:00:00+00:00",
    )

    imported = report.plates[0]
    assert imported.status == "imported"
    assert imported.source_raw_sha256 == imported.imported_raw_sha256
    assert imported.counts == {"wells": 96, "readings": 96, "calls": 96, "results": 4}
    assert imported.derived_differences == ()
    metadata = repository.connection.execute(
        "SELECT e.name, e.experiment_date, e.operator_name, e.reader, e.incubation_time_hours, "
        "e.inoculum_od, e.growth_phase, e.harvest_od, e.doubling_time_minutes, e.notes, "
        "p.plate_name, p.plate_format, p.threshold, p.threshold_method, p.background_method, "
        "p.is_locked, p.is_checked, p.deleted_at, p.legacy_run_id FROM experiments e "
        "JOIN plates p ON p.experiment_id = e.experiment_id"
    ).fetchone()
    assert metadata == (
        "Synthetic MIC fixture",
        "2026-01-03",
        "fixture-user",
        "Synthetic reader",
        18.0,
        0.01,
        "Exponential",
        0.5,
        30.0,
        "synthetic",
        "Synthetic MIC fixture",
        96,
        0.1,
        "fixed",
        "average_blanks",
        0,
        1,
        None,
        "synthetic-mic-plate",
    )
    a1 = repository.connection.execute(
        "SELECT w.position, w.custom_json, mr.value_raw, wc.strain, wc.treatment, "
        "wc.concentration, wc.concentration_unit, wc.medium, wc.replicate "
        "FROM wells w JOIN mic_readings mr ON mr.well_id = w.well_id "
        "JOIN well_conditions wc ON wc.well_id = w.well_id WHERE w.position = 'A1'"
    ).fetchone()
    assert a1 == (
        "A1",
        '{"batch":"B7","oxygen":"aerobic"}',
        0.25,
        "strain_normal",
        "compound_x",
        0.5,
        "ug/mL",
        "Synthetic medium",
        1,
    )
    results = repository.connection.execute(
        "SELECT strain, mic_operator, mic_value, warning FROM mic_results ORDER BY strain"
    ).fetchall()
    assert results[0][:3] == ("strain_all_growth", ">", 4.0)
    assert results[2][3] == "Growth bounce detected at 2.0 after no-growth at 1.0"
    event = repository.connection.execute(
        "SELECT actor_id, event_type, details_json FROM provenance_events"
    ).fetchone()
    assert event is not None
    assert event[:2] == (ACTOR.user_id, "legacy_mic_imported")
    details = json.loads(str(event[2]))
    assert len(details["legacy_results"]) == 4
    assert details["derived_differences"] == []
    assert repository.connection.execute(
        "SELECT source_kind, content_sha256, parser_version FROM import_sources"
    ).fetchone() == ("legacy_mic", source_hash, "legacy-mic-sqlite/1.0.0")

    duplicate = import_legacy_mic_file(source, repository, ACTOR)
    assert duplicate.plates[0].status == "skipped_duplicate_source"
    duplicate_commit = import_legacy_mic_file(source, repository, ACTOR, dry_run=False)
    assert duplicate_commit.plates[0].status == "skipped_duplicate_source"
    assert counts(repository)["plates"] == 1
    assert file_sha256(source) == source_hash


def test_deleted_locked_checked_state_uses_audited_deletion_surrogate(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute("UPDATE plates SET is_deleted = 1, is_locked = 1, is_checked = 1")

    report = import_legacy_mic_file(
        source,
        repository,
        ACTOR,
        dry_run=False,
        id_factory=id_sequence(),
        now_factory=lambda: "2026-08-01T12:00:00+00:00",
    )

    assert any("surrogate" in warning for warning in report.plates[0].warnings)
    state = repository.connection.execute(
        "SELECT is_locked, is_checked, deleted_at, deleted_by FROM plates"
    ).fetchone()
    assert state == (1, 1, "2026-08-01T12:00:00+00:00", ACTOR.user_id)
    details = json.loads(
        str(
            repository.connection.execute("SELECT details_json FROM provenance_events").fetchone()[
                0
            ]
        )
    )
    assert details["deletion_surrogate"] == {
        "actor_id": ACTOR.user_id,
        "occurred_at": "2026-08-01T12:00:00+00:00",
    }


def test_derived_difference_blocks_by_default_but_can_be_audited(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute("UPDATE mic_results SET mic_value = 999 WHERE strain = 'strain_normal'")

    preview = preview_legacy_mic_file(source)
    assert preview.plates[0].derived_differences == (
        "strain_normal/compound_x/Synthetic medium/1: mic_value differs",
    )
    with pytest.raises(LegacyMicValidationError, match="explicitly allow"):
        import_legacy_mic_file(source, repository, ACTOR, dry_run=False)
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)

    report = import_legacy_mic_file(
        source,
        repository,
        ACTOR,
        dry_run=False,
        allow_derived_differences=True,
        id_factory=id_sequence(),
    )
    assert report.plates[0].status == "imported"
    assert report.plates[0].derived_differences == preview.plates[0].derived_differences
    assert repository.connection.execute(
        "SELECT mic_value FROM mic_results WHERE strain = 'strain_normal'"
    ).fetchone() == (2.0,)


def test_forced_failure_rolls_back_and_invalid_schema_is_read_only(
    repository: SqlPlateReaderRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = copied_fixture(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced legacy MIC failure")

    monkeypatch.setattr(repository, "insert_mic_results", fail)
    with pytest.raises(RuntimeError, match="forced legacy MIC failure"):
        import_legacy_mic_file(source, repository, ACTOR, dry_run=False, id_factory=id_sequence())
    assert counts(repository)["plates"] == 0
    assert file_sha256(source) == FIXTURE_SHA256

    broken = tmp_path / "broken-mic.sqlite"
    shutil.copyfile(LEGACY_FIXTURE, broken)
    with sqlite3.connect(broken) as legacy:
        legacy.execute("DROP TABLE mic_results")
    broken_hash = file_sha256(broken)
    with pytest.raises(LegacyMicValidationError, match="missing tables"):
        preview_legacy_mic_file(broken)
    assert file_sha256(broken) == broken_hash


def copied_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "mic-legacy-copy.sqlite"
    shutil.copyfile(LEGACY_FIXTURE, destination)
    return destination


def id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 100_000))
    return lambda: f"legacy-mic-{next(counter):05d}"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(repository: SqlPlateReaderRepository) -> dict[str, int]:
    tables = (
        "users",
        "experiments",
        "plates",
        "wells",
        "well_conditions",
        "mic_readings",
        "analysis_revisions",
        "mic_well_calls",
        "mic_results",
        "import_sources",
        "provenance_events",
    )
    return {
        table: int(
            str(repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        )
        for table in tables
    }
