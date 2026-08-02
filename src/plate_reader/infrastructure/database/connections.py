"""Local, isolated fake-cloud, and remote Turso connection factories."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import turso

from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.infrastructure.database.migrations import apply_migrations


class DatabaseBackend(StrEnum):
    PYTURSO = "pyturso"
    FAKE_CLOUD = "fake-cloud"


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    path: Path
    backend: DatabaseBackend
    migrations_directory: Path


@dataclass(frozen=True, slots=True)
class TursoDatabaseConfig:
    """Secrets required by the over-the-wire Turso adapter.

    The values must come from environment/host secret storage and must never be
    rendered, logged, or persisted in an application database.
    """

    database_url: str
    auth_token: str
    migrations_directory: Path


def connect_database(config: DatabaseConfig, *, migrate: bool = True) -> Connection:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    if config.backend is DatabaseBackend.PYTURSO:
        connection = cast(Connection, turso.connect(str(config.path), isolation_level=None))
    else:
        connection = cast(
            Connection,
            sqlite3.connect(
                config.path,
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            ),
        )
    configure_connection(connection)
    if migrate:
        apply_migrations(connection, config.migrations_directory)
    return connection


def connect_turso_database(config: TursoDatabaseConfig, *, migrate: bool = True) -> Connection:
    """Open a direct remote Turso connection using the official libSQL driver."""

    database_url = _validated_turso_url(config.database_url)
    auth_token = config.auth_token.strip()
    if not auth_token:
        raise ValueError("Turso authentication token is required")
    connection = cast(
        Connection,
        _connect_libsql(
            database=database_url,
            auth_token=auth_token,
            timeout=10.0,
            isolation_level=None,
            _check_same_thread=False,
        ),
    )
    configure_connection(connection, enable_busy_timeout=False)
    if migrate:
        apply_migrations(connection, config.migrations_directory)
    return connection


def configure_connection(connection: Connection, *, enable_busy_timeout: bool = True) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    if enable_busy_timeout:
        connection.execute("PRAGMA busy_timeout = 10000")


def _validated_turso_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"libsql", "https"} or not parsed.hostname:
        raise ValueError("Turso database URL must use libsql:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Turso database URL must not contain credentials, query, or fragment")
    return url.rstrip("/")


def _connect_libsql(**kwargs: object) -> Any:
    """Import the cloud-only native driver lazily for smaller offline packages."""

    import libsql

    return libsql.connect(**kwargs)
