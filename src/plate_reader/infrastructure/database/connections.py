"""Local pyturso and isolated fake-cloud connection factories."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

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


def configure_connection(connection: Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
