"""Small, deterministic SQL migration loader shared by all DB-API adapters."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from plate_reader.infrastructure.database.dbapi import Connection

_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum_sha256: str


def discover_migrations(directory: Path) -> tuple[Migration, ...]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"Invalid migration filename: {path.name}")
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                sql=sql,
                checksum_sha256=hashlib.sha256(sql.encode()).hexdigest(),
            )
        )
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"Migration versions must be contiguous from 1; found {versions}")
    return tuple(migrations)


def apply_migrations(connection: Connection, directory: Path) -> tuple[int, ...]:
    """Apply pending migrations atomically and reject changed history."""
    connection.execute("PRAGMA foreign_keys = ON")
    migrations = discover_migrations(directory)
    applied_rows: list[tuple[int, str]] = []
    if _table_exists(connection, "schema_migrations"):
        applied_rows = connection.execute(
            "SELECT version, checksum_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
    applied = dict(applied_rows)
    for migration in migrations:
        previous_checksum = applied.get(migration.version)
        if previous_checksum is not None:
            if previous_checksum != migration.checksum_sha256:
                raise RuntimeError(f"Migration {migration.version} checksum changed")
            continue
        applied_at = datetime.now(UTC).isoformat()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in split_sql_statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, checksum_sha256, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                    applied_at,
                ),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
    return tuple(migration.version for migration in migrations if migration.version not in applied)


def split_sql_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise ValueError("Migration contains an incomplete SQL statement")
    return tuple(statements)


def _table_exists(connection: Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone()
    return row is not None
