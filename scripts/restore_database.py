"""Restore a complete logical backup to a new standard SQLite database."""

from __future__ import annotations

import argparse
from pathlib import Path

from plate_reader.infrastructure.database import restore_complete_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if not args.backup.is_file():
        parser.error(f"Backup does not exist: {args.backup}")
    root = Path(__file__).resolve().parents[1]
    report = restore_complete_database(args.backup, args.destination, root / "migrations")
    print(f"Verified restore created: {report.path}")
    print(f"Tables verified: {len(report.table_counts)}")


if __name__ == "__main__":
    main()
