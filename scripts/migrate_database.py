"""Apply ordered migrations explicitly to a local or fake-cloud database."""

from __future__ import annotations

import argparse
from pathlib import Path

from plate_reader.infrastructure.database import DatabaseBackend, DatabaseConfig, connect_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--backend",
        choices=tuple(DatabaseBackend),
        default=DatabaseBackend.FAKE_CLOUD,
        type=DatabaseBackend,
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    connection = connect_database(DatabaseConfig(args.database, args.backend, root / "migrations"))
    try:
        rows = connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        connection.close()
    print(f"Applied migrations to {args.database}:")
    for version, name, applied_at in rows:
        print(f"  {version}: {name} ({applied_at})")


if __name__ == "__main__":
    main()
