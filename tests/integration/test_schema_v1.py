from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from plate_reader.infrastructure.database.migrations import apply_migrations, discover_migrations

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
EXPECTED_TABLES = {
    "analysis_revisions",
    "experiment_tags",
    "experiments",
    "growth_backgrounds",
    "growth_measurements",
    "growth_series_chunks",
    "growth_metrics",
    "import_sources",
    "mic_readings",
    "mic_results",
    "mic_well_calls",
    "plate_templates",
    "plates",
    "provenance_events",
    "saved_options",
    "schema_migrations",
    "users",
    "well_conditions",
    "wells",
}


@pytest.fixture
def database() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection, MIGRATIONS)
    seed_plate(connection)
    try:
        yield connection
    finally:
        connection.close()


def seed_plate(connection: sqlite3.Connection) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    connection.execute(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("user-1", "fixture@example.invalid", "Fixture", "admin", 1, timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO experiments "
        "(experiment_id, name, project, experiment_date, custom_json, created_by, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "experiment-1",
            "Synthetic",
            "Contract",
            "2026-01-01",
            "{}",
            "user-1",
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        "INSERT INTO plates "
        "(plate_id, experiment_id, assay_type, plate_name, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("plate-1", "experiment-1", "growth", "Plate 1", "user-1", timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO wells "
        "(well_id, plate_id, position, row_index, column_index, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("well-1", "plate-1", "A1", 0, 0, timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO growth_measurements VALUES (?, ?, ?, ?, ?, ?)",
        ("plate-1", "well-1", "od600", 0, 0, 0.05),
    )
    connection.commit()


def test_schema_creates_from_empty_and_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    assert apply_migrations(connection, MIGRATIONS) == (1, 2, 3)
    assert apply_migrations(connection, MIGRATIONS) == ()
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == EXPECTED_TABLES
    assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()


def test_existing_custom_layout_columns_are_registered_by_migration(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    for filename in ("0001_schema_v1.sql", "0002_compact_growth_series.sql"):
        shutil.copyfile(MIGRATIONS / filename, migration_dir / filename)
    connection = sqlite3.connect(":memory:")
    assert apply_migrations(connection, migration_dir) == (1, 2)
    seed_plate(connection)
    connection.execute(
        "UPDATE wells SET custom_json = ? WHERE well_id = 'well-1'",
        (json.dumps({"Oxygen": "anaerobic", "t0_added_min": 5}),),
    )
    connection.commit()

    filename = "0003_register_existing_layout_columns.sql"
    shutil.copyfile(MIGRATIONS / filename, migration_dir / filename)
    assert apply_migrations(connection, migration_dir) == (3,)

    assert connection.execute(
        "SELECT option_type, value, created_by FROM saved_options"
    ).fetchall() == [("layout_column:growth", "Oxygen", "user-1")]
    connection.close()


def test_migration_history_validation(tmp_path: Path) -> None:
    invalid_name = tmp_path / "not-a-migration.sql"
    invalid_name.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid migration filename"):
        discover_migrations(tmp_path)

    invalid_name.unlink()
    (tmp_path / "0002_gap.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="contiguous"):
        discover_migrations(tmp_path)


def test_changed_or_broken_migration_is_rejected(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    original = MIGRATIONS / "0001_schema_v1.sql"
    copied = migration_dir / original.name
    copied.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection, migration_dir)

    copied.write_text(copied.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum changed"):
        apply_migrations(connection, migration_dir)

    copied.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    (migration_dir / "0002_broken.sql").write_text(
        "CREATE TABLE should_rollback(value TEXT); INVALID SQL;", encoding="utf-8"
    )
    with pytest.raises(sqlite3.Error):
        apply_migrations(connection, migration_dir)
    assert connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='should_rollback'"
    ).fetchone() == (0,)
    connection.close()


def test_raw_measurements_and_provenance_are_immutable(
    database: sqlite3.Connection,
) -> None:
    before = raw_measurement_hash(database)
    database.execute(
        "UPDATE plates SET plate_name = ?, updated_at = ? WHERE plate_id = ?",
        ("Renamed", "2026-01-02T00:00:00+00:00", "plate-1"),
    )
    assert raw_measurement_hash(database) == before

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        database.execute("UPDATE growth_measurements SET value_raw = 1 WHERE plate_id = 'plate-1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        database.execute("DELETE FROM growth_measurements WHERE plate_id = 'plate-1'")

    database.execute(
        "INSERT INTO provenance_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("event-1", "user-1", "rename", "plate", "plate-1", "2026-01-02", "{}"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        database.execute("DELETE FROM provenance_events WHERE event_id = 'event-1'")


def test_multirow_failure_rolls_back(database: sqlite3.Connection) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    with pytest.raises(sqlite3.IntegrityError), database:
        database.execute(
            "INSERT INTO wells "
            "(well_id, plate_id, position, row_index, column_index, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("well-2", "plate-1", "A2", 0, 1, timestamp, timestamp),
        )
        database.execute(
            "INSERT INTO wells "
            "(well_id, plate_id, position, row_index, column_index, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("well-3", "plate-1", "A1", 0, 2, timestamp, timestamp),
        )
    assert database.execute("SELECT count(*) FROM wells").fetchone() == (1,)


@pytest.mark.parametrize(
    ("sql", "parameters", "index_name"),
    [
        (
            "SELECT plate_id FROM plates WHERE deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT 100",
            (),
            "idx_plates_list",
        ),
        (
            "SELECT well_id, time_index, value_raw FROM growth_measurements "
            "WHERE plate_id = ? AND channel = ? ORDER BY time_index, well_id",
            ("plate-1", "od600"),
            "idx_growth_measurements_load",
        ),
        (
            "SELECT result_id FROM mic_results WHERE strain = ? AND treatment = ? AND medium = ?",
            ("strain", "compound", "medium"),
            "idx_mic_results_search",
        ),
        (
            "SELECT well_id FROM well_conditions WHERE strain = ? AND medium = ? AND treatment = ?",
            ("strain", "medium", "compound"),
            "idx_conditions_search",
        ),
    ],
)
def test_critical_queries_use_indexes(
    database: sqlite3.Connection,
    sql: str,
    parameters: tuple[str, ...],
    index_name: str,
) -> None:
    plan = " ".join(
        str(column)
        for row in database.execute(f"EXPLAIN QUERY PLAN {sql}", parameters)
        for column in row
    )
    assert index_name in plan


def test_typical_growth_run_is_compact(tmp_path: Path) -> None:
    path = tmp_path / "typical.sqlite"
    connection = sqlite3.connect(path)
    apply_migrations(connection, MIGRATIONS)
    seed_plate_without_well(connection)
    timestamp = "2026-01-01T00:00:00+00:00"
    wells = [
        (
            f"well-{row}{column}",
            "plate-1",
            f"{row}{column}",
            ord(row) - ord("A"),
            column - 1,
            timestamp,
            timestamp,
        )
        for row in "ABCDEFGH"
        for column in range(1, 13)
    ]
    connection.executemany(
        "INSERT INTO wells "
        "(well_id, plate_id, position, row_index, column_index, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        wells,
    )
    measurements = (
        (
            "plate-1",
            well_id,
            "od600",
            time_index,
            time_index * 600_000_000,
            round(0.05 + (well_index * 0.0001) + (time_index * 0.001), 6),
        )
        for well_index, (well_id, *_rest) in enumerate(wells)
        for time_index in range(145)
    )
    connection.executemany(
        "INSERT INTO growth_measurements VALUES (?, ?, ?, ?, ?, ?)", measurements
    )
    connection.commit()
    assert connection.execute("SELECT count(*) FROM growth_measurements").fetchone() == (13_920,)
    connection.execute("VACUUM")
    connection.close()
    assert path.stat().st_size < 3_000_000


def seed_plate_without_well(connection: sqlite3.Connection) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    connection.execute(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("user-1", "fixture@example.invalid", "Fixture", "admin", 1, timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO experiments "
        "(experiment_id, name, experiment_date, custom_json, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("experiment-1", "Synthetic", "2026-01-01", "{}", "user-1", timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO plates "
        "(plate_id, experiment_id, assay_type, plate_name, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("plate-1", "experiment-1", "growth", "Plate 1", "user-1", timestamp, timestamp),
    )
    connection.commit()


def raw_measurement_hash(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT plate_id, well_id, channel, time_index, elapsed_microseconds, "
        "value_raw FROM growth_measurements ORDER BY plate_id, well_id, channel, time_index"
    ).fetchall()
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
