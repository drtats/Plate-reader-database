"""A warm Streamlit resource cache must not retain obsolete repository methods."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from plate_reader.application.contracts import PlateId
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.runtime import LocalAppConfig, RuntimeInfo
from plate_reader.ui import context as context_module
from plate_reader.ui.context import CloudCredentials

ROOT = Path(__file__).resolve().parents[2]


class _LegacyRepository:
    """The cached pre-bulk-editor API, backed by a real test connection."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def transaction(self) -> AbstractContextManager[None]:
        return SqlPlateReaderRepository(self.connection).transaction()

    def upsert_user(self, values: Mapping[str, object]) -> str:
        return SqlPlateReaderRepository(self.connection).upsert_user(values)


@pytest.mark.parametrize("storage_mode", ("local", "fake-cloud"))
def test_local_context_refreshes_repository_code_without_reconnecting_or_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, storage_mode: str
) -> None:
    backend = DatabaseBackend.PYTURSO if storage_mode == "local" else DatabaseBackend.FAKE_CLOUD
    database_path = tmp_path / "reload.sqlite"
    migrations = ROOT / "migrations"
    config = LocalAppConfig(
        RuntimeInfo("development", storage_mode),
        database_path,
        "reload@example.invalid",
        "editor",
        True,
    )
    context_module._cached_context.clear()
    monkeypatch.setattr(context_module, "SqlPlateReaderRepository", _LegacyRepository)
    cached = context_module._cached_context(
        str(database_path),
        backend,
        str(migrations),
        config.development_user_email,
        config.development_user_role,
        config.writes_enabled,
    )
    try:
        with pytest.raises(AttributeError, match="growth_cultivation_metadata"):
            cached.repository.growth_cultivation_metadata((PlateId("missing"),))
        monkeypatch.setattr(context_module, "SqlPlateReaderRepository", SqlPlateReaderRepository)
        writes_before = cached.repository.connection.execute("SELECT total_changes()").fetchone()
        # Prove the fix doesn't reopen the database or rerun identity initialization.
        monkeypatch.setattr(context_module, "connect_database", _unexpected_connect)
        first = context_module.app_context(config, migrations)
        second = context_module.app_context(config, migrations)
        assert first.repository.growth_cultivation_metadata((PlateId("missing"),)) == ()
        assert second.repository.growth_cultivation_metadata((PlateId("missing"),)) == ()
        assert (
            first.repository.connection
            is second.repository.connection
            is cached.repository.connection
        )
        assert first.actor == second.actor == cached.actor
        assert (
            cached.repository.connection.execute("SELECT total_changes()").fetchone()
            == writes_before
        )
    finally:
        context_module._cached_context.clear()
        cached.repository.connection.close()


def test_cloud_context_refreshes_repository_code_without_clearing_live_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    migrations = ROOT / "migrations"
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "cloud-reload.sqlite",
            DatabaseBackend.FAKE_CLOUD,
            migrations,
        )
    )
    context_module._cached_cloud_repository.clear()
    monkeypatch.setattr(context_module, "SqlPlateReaderRepository", _LegacyRepository)
    monkeypatch.setattr(context_module, "connect_turso_database", lambda _config: connection)
    cached = context_module._cached_cloud_repository(
        "libsql://reload.example.invalid",
        str(migrations),
        hosted_user_email="",
        hosted_user_role="",
        _auth_token="synthetic-token",
    )
    try:
        with pytest.raises(AttributeError, match="growth_cultivation_metadata"):
            cached.growth_cultivation_metadata((PlateId("missing"),))
        monkeypatch.setattr(context_module, "SqlPlateReaderRepository", SqlPlateReaderRepository)
        monkeypatch.setattr(context_module, "connect_turso_database", _unexpected_connect)
        writes_before = connection.execute("SELECT total_changes()").fetchone()
        repository = context_module._healthy_cloud_repository(
            CloudCredentials("libsql://reload.example.invalid", "synthetic-token"),
            migrations,
            hosted_user_email="",
            hosted_user_role="",
        )
        assert repository.connection is cached.connection is connection
        assert repository.growth_cultivation_metadata((PlateId("missing"),)) == ()
        assert connection.execute("SELECT total_changes()").fetchone() == writes_before
    finally:
        context_module._cached_cloud_repository.clear()
        connection.close()


def _unexpected_connect(_config: object) -> None:
    raise AssertionError("Refreshing repository methods must reuse the existing connection")
