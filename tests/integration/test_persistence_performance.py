from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import Actor, ImportGrowthRun, Role, UserId
from plate_reader.application.demo import synthetic_growth_csv
from plate_reader.application.services import ImportGrowthRunService
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
SAVE_BUDGET_SECONDS = 1.5
LOAD_BUDGET_SECONDS = 0.25
FILE_BUDGET_BYTES = 1 * 1024 * 1024


@pytest.mark.parametrize("backend", tuple(DatabaseBackend), ids=lambda backend: backend.value)
def test_full_growth_run_stays_within_measured_regression_budget(
    backend: DatabaseBackend, tmp_path: Path
) -> None:
    path = tmp_path / f"performance-{backend.value}.sqlite"
    csv_text = synthetic_growth_csv()
    connection = connect_database(DatabaseConfig(path, backend, MIGRATIONS))
    repository = SqlPlateReaderRepository(connection)
    command = ImportGrowthRun(
        actor=Actor(UserId("performance-user"), "performance@example.invalid", Role.EDITOR),
        source_name="synthetic-growth-24h.csv",
        source_sha256=hashlib.sha256(csv_text.encode()).hexdigest(),
        parser_version=GROWTH_NORMALIZATION_VERSION,
        experiment_name="Persistence regression test",
        plate_name="Full 96-well plate",
        experiment_date=date(2026, 1, 1),
    )

    save_started = time.perf_counter()
    result = ImportGrowthRunService(repository, id_factory=identifier_sequence()).execute(
        command, csv_text
    )
    save_seconds = time.perf_counter() - save_started
    load_started = time.perf_counter()
    snapshot = repository.load_plate(result.plate_id)
    load_seconds = time.perf_counter() - load_started
    legacy_count = connection.execute("SELECT count(*) FROM growth_measurements").fetchone()
    chunk_count = connection.execute("SELECT count(*) FROM growth_series_chunks").fetchone()
    connection.close()

    assert snapshot is not None
    assert len(snapshot.raw_observations) == 13_920
    assert legacy_count == (0,)
    assert chunk_count == (1,)
    assert save_seconds < SAVE_BUDGET_SECONDS
    assert load_seconds < LOAD_BUDGET_SECONDS
    assert path.stat().st_size < FILE_BUDGET_BYTES


def identifier_sequence() -> Callable[[], str]:
    counter = iter(range(100_000))
    return lambda: f"perf-{next(counter):06d}"
