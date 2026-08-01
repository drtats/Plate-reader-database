"""Operate a real Turso database without accepting secrets on the command line."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from plate_reader.application.contracts import Role
from plate_reader.infrastructure.database import (
    SqlPlateReaderRepository,
    TursoDatabaseConfig,
    backup_complete_database,
    connect_turso_database,
    restore_complete_database_to_connection,
)
from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.infrastructure.database.portable import TABLE_COLUMNS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("migrate", help="Apply and report ordered migrations")
    subcommands.add_parser("status", help="Report schema and logical table counts")

    backup = subcommands.add_parser("backup", help="Create a verified local SQLite backup")
    backup.add_argument("destination", type=Path)

    restore = subcommands.add_parser(
        "restore", help="Restore a verified backup into an empty Turso database"
    )
    restore.add_argument("backup", type=Path)
    restore.add_argument(
        "--confirm-empty-target",
        action="store_true",
        help="Required acknowledgement; existing application rows are never overwritten",
    )

    bootstrap = subcommands.add_parser(
        "bootstrap-admin", help="Create the first authorized administrator"
    )
    bootstrap.add_argument("email")
    bootstrap.add_argument("--display-name", required=True)

    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = _config_from_environment(root / "migrations")
    if args.command == "restore" and not args.confirm_empty_target:
        parser.error("restore requires --confirm-empty-target")
    if args.command == "backup" and args.destination.exists():
        parser.error(f"Backup destination already exists: {args.destination}")
    if args.command == "restore" and not args.backup.is_file():
        parser.error(f"Backup does not exist: {args.backup}")

    connection = connect_turso_database(config, migrate=args.command != "backup")
    try:
        if args.command == "migrate":
            _print_migrations(connection)
        elif args.command == "status":
            _print_status(connection)
        elif args.command == "backup":
            backup_complete_database(connection, args.destination, root / "migrations")
            print(f"Verified complete backup created: {args.destination}")
        elif args.command == "restore":
            report = restore_complete_database_to_connection(args.backup, connection)
            print(f"Verified remote restore completed across {len(report.table_counts)} tables")
        else:
            _bootstrap_admin(connection, args.email, args.display_name)
    finally:
        connection.close()


def _config_from_environment(migrations_directory: Path) -> TursoDatabaseConfig:
    database_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    auth_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if not database_url or not auth_token:
        raise SystemExit(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in the process environment"
        )
    return TursoDatabaseConfig(database_url, auth_token, migrations_directory)


def _print_migrations(connection: Connection) -> None:
    rows = connection.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    print("Applied migrations:")
    for version, name, applied_at in rows:
        print(f"  {version}: {name} ({applied_at})")


def _print_status(connection: Connection) -> None:
    print("Logical table counts:")
    for table in TABLE_COLUMNS:
        count_row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
        count = 0 if count_row is None else count_row[0]
        print(f"  {table}: {count}")


def _bootstrap_admin(connection: Connection, email: str, display_name: str) -> None:
    normalized_email = email.strip().casefold()
    if "@" not in normalized_email or not display_name.strip():
        raise SystemExit("A valid email and non-empty display name are required")
    repository = SqlPlateReaderRepository(connection)
    if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
        raise SystemExit("Bootstrap is disabled after the first user exists")
    with repository.transaction():
        repository.upsert_user(
            {
                "email": normalized_email,
                "display_name": display_name.strip(),
                "role": Role.ADMIN,
                "is_active": True,
            }
        )
    print(f"Initial administrator registered: {normalized_email}")


if __name__ == "__main__":
    main()
