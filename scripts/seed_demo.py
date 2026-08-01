"""Seed a local or fake-cloud database with a deterministic growth run."""

from __future__ import annotations

import argparse
import hashlib
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
    parser.add_argument("database", type=Path, help="Destination database path")
    parser.add_argument(
        "--backend",
        choices=tuple(DatabaseBackend),
        default=DatabaseBackend.FAKE_CLOUD,
        type=DatabaseBackend,
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    csv_text = synthetic_growth_csv()
    connection = connect_database(DatabaseConfig(args.database, args.backend, root / "migrations"))
    try:
        result = ImportGrowthRunService(SqlPlateReaderRepository(connection)).execute(
            ImportGrowthRun(
                actor=Actor(UserId("demo-editor"), "demo@example.invalid", Role.EDITOR),
                source_name="synthetic-growth-24h.csv",
                source_sha256=hashlib.sha256(csv_text.encode()).hexdigest(),
                parser_version=GROWTH_NORMALIZATION_VERSION,
                experiment_name="Synthetic 24-hour growth experiment",
                plate_name="Synthetic 96-well plate",
                experiment_date=date(2026, 1, 1),
            ),
            csv_text,
        )
    finally:
        connection.close()
    action = "created" if result.created else "already present"
    print(f"{action}: plate={result.plate_id} measurements={result.measurement_count}")


if __name__ == "__main__":
    main()
