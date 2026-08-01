"""Measure the Phase 3 full-run persistence workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from datetime import date
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")

    root = Path(__file__).resolve().parents[1]
    results = [measure(root, backend, args.repetitions) for backend in DatabaseBackend]
    print(json.dumps(results, indent=2, sort_keys=True))


def measure(root: Path, backend: DatabaseBackend, repetitions: int) -> dict[str, object]:
    csv_text = synthetic_growth_csv()
    digest = hashlib.sha256(csv_text.encode()).hexdigest()
    saves: list[float] = []
    loads: list[float] = []
    sizes: list[int] = []
    with tempfile.TemporaryDirectory(prefix=f"plate-reader-{backend.value}-") as directory:
        for repetition in range(repetitions):
            path = Path(directory) / f"run-{repetition}.sqlite"
            connection = connect_database(DatabaseConfig(path, backend, root / "migrations"))
            repository = SqlPlateReaderRepository(connection)
            start = time.perf_counter()
            result = ImportGrowthRunService(repository).execute(
                ImportGrowthRun(
                    actor=Actor(
                        UserId(f"benchmark-{repetition}"),
                        f"benchmark-{repetition}@example.invalid",
                        Role.EDITOR,
                    ),
                    source_name="synthetic-growth-24h.csv",
                    source_sha256=digest,
                    parser_version=GROWTH_NORMALIZATION_VERSION,
                    experiment_name="Persistence benchmark",
                    plate_name=f"Benchmark plate {repetition}",
                    experiment_date=date(2026, 1, 1),
                    idempotency_key=f"benchmark:{backend.value}:{repetition}",
                ),
                csv_text,
            )
            saves.append(time.perf_counter() - start)
            start = time.perf_counter()
            snapshot = repository.load_plate(result.plate_id)
            loads.append(time.perf_counter() - start)
            if snapshot is None or len(snapshot.raw_observations) != 13_920:
                raise RuntimeError("Benchmark load did not return the complete run")
            connection.close()
            sizes.append(path.stat().st_size)
    return {
        "backend": backend.value,
        "measurements": 13_920,
        "repetitions": repetitions,
        "save_seconds": summary(saves),
        "load_seconds": summary(loads),
        "database_bytes": summary([float(value) for value in sizes]),
    }


def summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "min": round(ordered[0], 6),
        "median": round(ordered[len(ordered) // 2], 6),
        "p95": round(ordered[p95_index], 6),
        "max": round(ordered[-1], 6),
    }


if __name__ == "__main__":
    main()
