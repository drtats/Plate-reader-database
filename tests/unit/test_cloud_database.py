from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from plate_reader.infrastructure.database import (
    TursoDatabaseConfig,
    connect_turso_database,
    connections,
)


def test_remote_turso_factory_uses_official_driver_and_applies_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def connect(**kwargs: object) -> sqlite3.Connection:
        captured.update(kwargs)
        return sqlite3.connect(":memory:", isolation_level=None)

    monkeypatch.setattr(connections, "_connect_libsql", connect)
    root = Path(__file__).resolve().parents[2]
    connection = connect_turso_database(
        TursoDatabaseConfig(
            "libsql://example-database.turso.io/",
            "test-token-never-logged",
            root / "migrations",
        )
    )
    try:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)
    finally:
        connection.close()

    assert captured == {
        "database": "libsql://example-database.turso.io",
        "auth_token": "test-token-never-logged",
        "timeout": 10.0,
        "isolation_level": None,
        "_check_same_thread": False,
    }


@pytest.mark.parametrize(
    "url",
    (
        "",
        "http://database.turso.io",
        "file:///tmp/database.sqlite",
        "libsql://user:secret@database.turso.io",
        "https://database.turso.io?authToken=secret",
        "https://database.turso.io/#secret",
    ),
)
def test_remote_turso_factory_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError, match="Turso database URL"):
        connect_turso_database(TursoDatabaseConfig(url, "token", Path("migrations")))


def test_remote_turso_factory_requires_token_without_echoing_it() -> None:
    with pytest.raises(ValueError, match="token") as error:
        connect_turso_database(
            TursoDatabaseConfig("libsql://database.turso.io", "   ", Path("migrations"))
        )
    assert "libsql://" not in str(error.value)
