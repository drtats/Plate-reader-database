"""Create an integrity-checked complete logical backup."""

from __future__ import annotations

import argparse
from pathlib import Path

from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    backup_complete_database,
    connect_database,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--backend",
        choices=tuple(DatabaseBackend),
        default=DatabaseBackend.FAKE_CLOUD,
        type=DatabaseBackend,
    )
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f"Database does not exist: {args.database}")
    root = Path(__file__).resolve().parents[1]
    connection = connect_database(DatabaseConfig(args.database, args.backend, root / "migrations"))
    try:
        backup_complete_database(connection, args.destination, root / "migrations")
    finally:
        connection.close()
    print(f"Verified backup created: {args.destination}")


if __name__ == "__main__":
    main()
