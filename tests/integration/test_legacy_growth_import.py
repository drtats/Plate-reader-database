from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import plate_reader.infrastructure.importers.legacy_growth as legacy_growth_module
from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.application.services.authorization import AuthorizationError
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.importers.legacy_growth import (
    LegacyGrowthValidationError,
    import_legacy_growth_file,
    preview_legacy_growth_file,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
LEGACY_FIXTURE = ROOT / "tests" / "fixtures" / "legacy" / "growth_v4.sqlite"
FIXTURE_SHA256 = "f964791d0c7a389010ff812119c61c7886a803f5946d78186a1d7391a931fc5a"
ACTOR = Actor(UserId("migration-editor"), "migration@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"legacy-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": ACTOR.user_id,
                "email": ACTOR.email,
                "display_name": "Migration Editor",
                "role": ACTOR.role,
                "is_active": True,
            }
        )
    try:
        yield repository
    finally:
        connection.close()


def test_preview_detects_v4_and_reports_mapping_gaps(tmp_path: Path) -> None:
    source = copied_fixture(tmp_path)

    preview = preview_legacy_growth_file(source)

    assert preview.detected_version == "growth-sqlite-v4"
    assert preview.file_sha256 == FIXTURE_SHA256
    assert preview.byte_size == LEGACY_FIXTURE.stat().st_size
    assert len(preview.runs) == 1
    run = preview.runs[0]
    assert run.run_id == "synthetic-growth-v4"
    assert run.experiment_name == "Synthetic growth fixture"
    assert (run.well_count, run.measurement_count, run.background_count) == (96, 384, 8)
    assert run.errors == ()
    assert run.missing_target_fields == (
        "project",
        "instrument",
        "temperature",
        "temperature_unit",
        "manual_subtraction",
    )
    assert "Legacy source has no values for" in run.warnings[0]
    assert file_sha256(source) == FIXTURE_SHA256


def test_dry_run_writes_nothing(repository: SqlPlateReaderRepository, tmp_path: Path) -> None:
    source = copied_fixture(tmp_path)
    before = table_counts(repository)

    report = import_legacy_growth_file(source, repository, ACTOR)

    assert report.dry_run is True
    assert report.source_unchanged is True
    assert report.runs[0].status == "ready"
    assert report.runs[0].plate_id is None
    assert report.runs[0].counts == {"wells": 96, "measurements": 384, "backgrounds": 8}
    assert table_counts(repository) == before
    assert file_sha256(source) == FIXTURE_SHA256


def test_commit_is_lossless_auditable_and_idempotent(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)

    report = import_legacy_growth_file(
        source, repository, ACTOR, dry_run=False, id_factory=id_sequence()
    )

    imported = report.runs[0]
    assert imported.status == "imported"
    assert imported.plate_id == "migration-0002"
    assert imported.source_raw_sha256 == imported.imported_raw_sha256
    assert imported.counts == {"wells": 96, "measurements": 384, "backgrounds": 8}
    assert report.source_unchanged is True
    assert file_sha256(source) == FIXTURE_SHA256
    plate = repository.connection.execute(
        "SELECT p.legacy_run_id, p.channel, p.instrument, p.temperature, p.custom_json, "
        "e.name, e.experiment_date, e.operator_name, e.project, e.custom_json "
        "FROM plates p JOIN experiments e ON e.experiment_id = p.experiment_id "
        "WHERE p.plate_id = ?",
        (imported.plate_id,),
    ).fetchone()
    assert plate is not None
    assert plate[:4] == ("synthetic-growth-v4", "od600", None, None)
    assert plate[5:9] == (
        "Synthetic growth fixture",
        "2026-01-02",
        "fixture-user",
        None,
    )
    assert json.loads(str(plate[4]))["legacy_plate_meta"]["meta_hash"] == "synthetic-meta-hash"
    assert json.loads(str(plate[9]))["legacy_plate_meta"]["od_csv_sha256"] == ("synthetic-od-hash")
    a1 = repository.connection.execute(
        "SELECT w.position, w.display_name, w.is_blank, w.background_group, w.plot_selected, "
        "w.notes, w.raw_label, w.custom_json, c.medium, c.strain, c.inoculum_size, "
        "c.treatment, c.replicate FROM wells w JOIN well_conditions c ON c.well_id = w.well_id "
        "WHERE w.plate_id = ? AND w.position = 'A1'",
        (imported.plate_id,),
    ).fetchone()
    assert a1 is not None
    assert a1[:7] == ("A1", "sample_A1", 1, "valid", 0, "synthetic", "sample_A1")
    assert json.loads(str(a1[7])) == {
        "col": 1,
        "notes": "synthetic",
        "plot": False,
        "raw_label": "sample_A1",
        "replicate": 1,
        "row": "A",
    }
    assert a1[8:] == (
        "Synthetic medium",
        "Synthetic strain",
        0.01,
        "compound_x",
        1,
    )
    snapshot = repository.load_plate(imported.plate_id)
    assert snapshot is not None
    a1_well_id = next(str(well["well_id"]) for well in snapshot.wells if well["position"] == "A1")
    curve = [
        (row["elapsed_microseconds"], row["value_raw"])
        for row in snapshot.raw_observations
        if row["well_id"] == a1_well_id
    ]
    assert curve == [
        (0, 0.05),
        (600_000_000, 0.051),
        (1_200_000_000, 0.052),
        (1_800_000_000, 0.053),
    ]
    backgrounds = repository.connection.execute(
        "SELECT background_group, elapsed_microseconds, mean_value, blank_count, qc_status "
        "FROM growth_backgrounds ORDER BY background_group, elapsed_microseconds"
    ).fetchall()
    assert len(backgrounds) == 8
    assert backgrounds[0] == ("high_cv", 0, 0.05, 2, "high_cv")
    assert backgrounds[4] == ("valid", 0, 0.0505, 2, "good")
    source_row = repository.connection.execute(
        "SELECT source_kind, content_sha256, parser_version, status, imported_by "
        "FROM import_sources"
    ).fetchone()
    assert source_row == (
        "legacy_growth",
        FIXTURE_SHA256,
        "legacy-growth-sqlite/1.0.0",
        "imported",
        ACTOR.user_id,
    )
    event = repository.connection.execute(
        "SELECT actor_id, event_type, details_json FROM provenance_events"
    ).fetchone()
    assert event is not None
    assert event[:2] == (ACTOR.user_id, "legacy_growth_imported")
    details = json.loads(str(event[2]))
    assert details["legacy_run_id"] == "synthetic-growth-v4"
    assert details["source_raw_sha256"] == imported.source_raw_sha256
    assert details["missing_target_fields"] == list(report.source.runs[0].missing_target_fields)
    assert_seeded_samples_match(source, repository, imported.plate_id)

    duplicate_preview = import_legacy_growth_file(source, repository, ACTOR)
    assert duplicate_preview.runs[0].status == "skipped_duplicate_source"
    assert duplicate_preview.runs[0].imported_raw_sha256 == imported.source_raw_sha256
    duplicate_commit = import_legacy_growth_file(source, repository, ACTOR, dry_run=False)
    assert duplicate_commit.runs[0].status == "skipped_duplicate_source"
    assert table_counts(repository) == {
        "users": 1,
        "experiments": 1,
        "plates": 1,
        "wells": 96,
        "well_conditions": 96,
        "growth_measurements": 384,
        "growth_backgrounds": 8,
        "import_sources": 1,
        "provenance_events": 1,
    }


def test_duplicate_run_id_from_changed_copy_is_diagnosed(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    import_legacy_growth_file(source, repository, ACTOR, dry_run=False, id_factory=id_sequence())
    with sqlite3.connect(source) as legacy:
        legacy.execute(
            "UPDATE measurements SET value_raw = value_raw + 0.1 "
            "WHERE run_id = 'synthetic-growth-v4' AND well = 'A1' AND time_min = 0"
        )

    report = import_legacy_growth_file(source, repository, ACTOR)

    assert report.runs[0].status == "skipped_duplicate_run_id"
    assert report.runs[0].source_raw_sha256 != report.runs[0].imported_raw_sha256
    assert "different raw hash" in report.runs[0].warnings[-1]
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (1,)


def test_forced_failure_rolls_back_and_source_stays_unchanged(
    repository: SqlPlateReaderRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = copied_fixture(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced legacy measurement failure")

    monkeypatch.setattr(repository, "insert_raw_observations", fail)
    with pytest.raises(RuntimeError, match="forced legacy measurement failure"):
        import_legacy_growth_file(
            source, repository, ACTOR, dry_run=False, id_factory=id_sequence()
        )

    assert table_counts(repository) == {
        "users": 1,
        "experiments": 0,
        "plates": 0,
        "wells": 0,
        "well_conditions": 0,
        "growth_measurements": 0,
        "growth_backgrounds": 0,
        "import_sources": 0,
        "provenance_events": 0,
    }
    assert file_sha256(source) == FIXTURE_SHA256


def test_commit_requires_registered_editor(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    viewer = Actor(UserId("migration-editor"), ACTOR.email, Role.VIEWER)

    with pytest.raises(AuthorizationError):
        import_legacy_growth_file(source, repository, viewer, dry_run=False)
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)


def test_schema_detection_and_validation_are_read_only(tmp_path: Path) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute("CREATE TABLE harmless_extension(value TEXT)")
        legacy.execute("ALTER TABLE plate_meta DROP COLUMN app_version")
    changed_hash = file_sha256(source)

    assert preview_legacy_growth_file(source).detected_version == "growth-sqlite-pre-v4"
    assert file_sha256(source) == changed_hash

    broken = tmp_path / "broken.sqlite"
    shutil.copyfile(LEGACY_FIXTURE, broken)
    with sqlite3.connect(broken) as legacy:
        legacy.execute("DROP TABLE measurements")
    broken_hash = file_sha256(broken)
    with pytest.raises(LegacyGrowthValidationError, match="missing tables"):
        preview_legacy_growth_file(broken)
    assert file_sha256(broken) == broken_hash


def test_missing_date_and_invalid_custom_json_are_reported(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute("UPDATE plate_meta SET experiment_date = NULL")
        legacy.execute("UPDATE well_meta SET custom_json = '{broken' WHERE well = 'A1'")

    preview = preview_legacy_growth_file(source)

    assert any("Missing experiment date" in error for error in preview.runs[0].errors)
    assert any("A1: custom_json is invalid" in warning for warning in preview.runs[0].warnings)
    dry_run = import_legacy_growth_file(source, repository, ACTOR)
    assert dry_run.runs[0].status == "blocked"
    with pytest.raises(LegacyGrowthValidationError, match="Missing experiment date"):
        import_legacy_growth_file(source, repository, ACTOR, dry_run=False)
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)


def test_optional_legacy_tables_and_malformed_optional_metadata_are_safe(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute("DROP TABLE backgrounds")
        legacy.execute("DROP TABLE provenance")
        legacy.execute(
            "UPDATE well_meta SET custom_json = '{broken', inoculum_size = 'not-a-number' "
            "WHERE well = 'A1'"
        )
        legacy.execute("UPDATE well_meta SET custom_json = '[]' WHERE well = 'B1'")
        legacy.execute(
            "UPDATE well_meta SET custom_json = NULL, inoculum_size = NULL WHERE well = 'C1'"
        )
        legacy.execute(
            "UPDATE well_meta SET custom_json = '{\"replicate\": false}' WHERE well = 'D1'"
        )

    report = import_legacy_growth_file(
        source, repository, ACTOR, dry_run=False, id_factory=id_sequence()
    )

    assert report.runs[0].status == "imported"
    assert report.runs[0].counts["backgrounds"] == 0
    assert any("invalid inoculum_size" in warning for warning in report.runs[0].warnings)
    assert any("invalid replicate" in warning for warning in report.runs[0].warnings)
    rows = repository.connection.execute(
        "SELECT w.position, w.custom_json, c.inoculum_size, c.replicate FROM wells w "
        "JOIN well_conditions c ON c.well_id = w.well_id "
        "WHERE w.position IN ('A1', 'B1', 'C1', 'D1') ORDER BY w.position"
    ).fetchall()
    assert json.loads(str(rows[0][1])) == {"_legacy_custom_json_raw": "{broken"}
    assert rows[0][2:] == (None, 1)
    assert json.loads(str(rows[1][1])) == {"_legacy_custom_json_raw": "[]"}
    assert rows[2][2] is None
    assert rows[3][3] == 1
    details = json.loads(
        str(
            repository.connection.execute("SELECT details_json FROM provenance_events").fetchone()[
                0
            ]
        )
    )
    assert details["legacy_provenance"] == []


def test_background_qc_boundaries_are_preserved(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute(
            "UPDATE backgrounds SET bg_cv = NULL WHERE bg_group = 'valid' AND time_min = 0"
        )
        legacy.execute(
            "UPDATE backgrounds SET bg_cv = 0.07 WHERE bg_group = 'valid' AND time_min = 10"
        )

    import_legacy_growth_file(source, repository, ACTOR, dry_run=False, id_factory=id_sequence())

    statuses = repository.connection.execute(
        "SELECT elapsed_microseconds, qc_status FROM growth_backgrounds "
        "WHERE background_group = 'valid' ORDER BY elapsed_microseconds"
    ).fetchall()
    assert statuses[:2] == [(0, "missing"), (600_000_000, "caution")]


def test_preview_reports_structural_data_problems(tmp_path: Path) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.execute("UPDATE well_meta SET well = 'Z99' WHERE well = 'A1'")
        legacy.execute(
            "INSERT INTO measurements SELECT * FROM measurements WHERE well = 'B1' AND time_min = 0"
        )

    run = preview_legacy_growth_file(source).runs[0]

    assert "Invalid 96-well position: Z99" in run.errors
    assert "Duplicate measurement identity" in run.errors
    assert any("unknown wells" in error for error in run.errors)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("ALTER TABLE plate_meta RENAME COLUMN experiment_name TO unsupported", "plate_meta"),
        ("ALTER TABLE measurements RENAME COLUMN value_raw TO unsupported", "measurement"),
        ("ALTER TABLE well_meta RENAME COLUMN well TO unsupported", "well_meta"),
        (
            "ALTER TABLE plate_meta DROP COLUMN app_version; "
            "ALTER TABLE plate_meta RENAME COLUMN experiment_date TO unsupported",
            "cannot be identified",
        ),
    ),
)
def test_unsupported_column_shapes_are_rejected(
    tmp_path: Path, mutation: str, message: str
) -> None:
    source = copied_fixture(tmp_path)
    with sqlite3.connect(source) as legacy:
        legacy.executescript(mutation)
    changed_hash = file_sha256(source)

    with pytest.raises(LegacyGrowthValidationError, match=message):
        preview_legacy_growth_file(source)
    assert file_sha256(source) == changed_hash


def test_empty_library_and_invalid_times_are_rejected(tmp_path: Path) -> None:
    empty = copied_fixture(tmp_path)
    with sqlite3.connect(empty) as legacy:
        legacy.execute("DELETE FROM plate_meta")
    with pytest.raises(LegacyGrowthValidationError, match="contains no runs"):
        preview_legacy_growth_file(empty)

    for index, invalid in enumerate((-1, "bad")):
        source = tmp_path / f"invalid-time-{index}.sqlite"
        shutil.copyfile(LEGACY_FIXTURE, source)
        with sqlite3.connect(source) as legacy:
            legacy.execute(
                "UPDATE measurements SET time_min = ? WHERE well = 'A1' AND time_min = 0",
                (invalid,),
            )
        with pytest.raises(LegacyGrowthValidationError, match="time_min"):
            preview_legacy_growth_file(source)


def test_verification_failures_roll_back_target_and_leave_source_unchanged(
    repository: SqlPlateReaderRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = copied_fixture(tmp_path)
    monkeypatch.setattr(legacy_growth_module, "_destination_raw_hash", lambda *_args: "mismatch")

    with pytest.raises(LegacyGrowthValidationError, match="Raw verification failed"):
        import_legacy_growth_file(
            source, repository, ACTOR, dry_run=False, id_factory=id_sequence()
        )

    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)
    assert file_sha256(source) == FIXTURE_SHA256


def copied_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "growth-v4-copy.sqlite"
    shutil.copyfile(LEGACY_FIXTURE, destination)
    return destination


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"migration-{next(counter):04d}"


def table_counts(repository: SqlPlateReaderRepository) -> dict[str, int]:
    counts = {
        table: int(
            str(repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        )
        for table in (
            "users",
            "experiments",
            "plates",
            "wells",
            "well_conditions",
            "growth_measurements",
            "growth_backgrounds",
            "import_sources",
            "provenance_events",
        )
    }
    counts["growth_measurements"] = sum(
        len(chunk)
        for (plate_id,) in repository.connection.execute(
            "SELECT plate_id FROM plates WHERE assay_type = 'growth'"
        ).fetchall()
        for chunk in repository.stream_growth_measurements(plate_id)
    )
    return counts


def assert_seeded_samples_match(
    source: Path, repository: SqlPlateReaderRepository, plate_id: str
) -> None:
    positions = [f"{row}{column}" for row in "ABCDEFGH" for column in range(1, 13)]
    selected = random.Random(20260801).sample(positions, 8)
    snapshot = repository.load_plate(plate_id)
    assert snapshot is not None
    positions_by_well = {str(well["well_id"]): str(well["position"]) for well in snapshot.wells}
    with sqlite3.connect(source) as legacy:
        legacy.row_factory = sqlite3.Row
        for position in selected:
            source_well = legacy.execute(
                "SELECT display_name, media, strain, inoculum_size, treatments, is_blank, "
                "bg_group, custom_json FROM well_meta WHERE well = ?",
                (position,),
            ).fetchone()
            target_well = repository.connection.execute(
                "SELECT w.display_name, c.medium, c.strain, c.inoculum_size, c.treatment, "
                "w.is_blank, w.background_group, w.custom_json FROM wells w "
                "JOIN well_conditions c ON c.well_id = w.well_id "
                "WHERE w.plate_id = ? AND w.position = ?",
                (plate_id, position),
            ).fetchone()
            assert source_well is not None and target_well is not None
            expected_well = (
                source_well[0],
                source_well[1],
                source_well[2],
                None if source_well[3] is None else float(source_well[3]),
                source_well[4],
                int(source_well[5]),
                source_well[6],
            )
            assert expected_well == tuple(target_well[:7])
            assert json.loads(str(source_well[7])) == json.loads(str(target_well[7]))
            source_curve = legacy.execute(
                "SELECT signal_type, time_min, value_raw FROM measurements "
                "WHERE well = ? ORDER BY signal_type, time_min",
                (position,),
            ).fetchall()
            target_curve = sorted(
                (
                    str(row["channel"]),
                    int(row["elapsed_microseconds"]),
                    row["value_raw"],
                )
                for row in snapshot.raw_observations
                if positions_by_well[str(row["well_id"])] == position
            )
            assert [
                (str(row[0]), round(float(row[1]) * 60_000_000), row[2]) for row in source_curve
            ] == list(target_curve)
