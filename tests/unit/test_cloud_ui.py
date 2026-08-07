from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from plate_reader.application.contracts import Role
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.runtime import LocalAppConfig, RuntimeInfo
from plate_reader.ui import context as context_module
from plate_reader.ui.cloud import load_cloud_credentials, oidc_provider
from plate_reader.ui.context import CloudCredentials, app_context


def test_cloud_credentials_prefer_environment_and_never_use_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://environment.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "environment-token")

    assert load_cloud_credentials(
        {
            "TURSO_DATABASE_URL": "libsql://secret-store.turso.io",
            "TURSO_AUTH_TOKEN": "secret-store-token",
        }
    ) == CloudCredentials("libsql://environment.turso.io", "environment-token")


def test_cloud_credentials_require_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TURSO_AUTH_TOKEN"):
        load_cloud_credentials({"TURSO_DATABASE_URL": "libsql://database.turso.io"})


def test_oidc_provider_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATE_READER_OIDC_PROVIDER", "Microsoft")
    assert oidc_provider() == "microsoft"
    monkeypatch.setenv("PLATE_READER_OIDC_PROVIDER", "not_valid")
    with pytest.raises(ValueError, match="OIDC_PROVIDER"):
        oidc_provider()


def test_cloud_context_resolves_only_pre_registered_database_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "cloud-double.sqlite", DatabaseBackend.FAKE_CLOUD, root / "migrations"
        )
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": "user-1",
                "email": "scientist@example.invalid",
                "display_name": "Scientist",
                "role": Role.VIEWER,
                "is_active": True,
            }
        )
    monkeypatch.setattr(
        context_module, "_cached_cloud_repository", lambda *args, **kwargs: repository
    )
    config = LocalAppConfig(
        RuntimeInfo("production", "cloud"),
        tmp_path / "unused.sqlite",
        "developer@example.invalid",
        "admin",
        True,
    )
    claims = {
        "sub": "provider-subject",
        "email": "Scientist@Example.Invalid",
        "email_verified": True,
        "exp": int(datetime.now(UTC).timestamp()) + 600,
    }

    context = app_context(
        config,
        root / "migrations",
        cloud_credentials=CloudCredentials("libsql://database.turso.io", "token"),
        oidc_claims=claims,
    )
    assert context.actor.email == "scientist@example.invalid"
    assert context.actor.role is Role.VIEWER
    connection.close()


def test_cloud_read_only_rollback_downgrades_database_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "cloud-admin.sqlite", DatabaseBackend.FAKE_CLOUD, root / "migrations"
        )
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": "admin-1",
                "email": "admin@example.invalid",
                "display_name": "Admin",
                "role": Role.ADMIN,
                "is_active": True,
            }
        )
    monkeypatch.setattr(
        context_module, "_cached_cloud_repository", lambda *args, **kwargs: repository
    )
    config = LocalAppConfig(
        RuntimeInfo("production", "cloud"),
        tmp_path / "unused.sqlite",
        "developer@example.invalid",
        "admin",
        False,
    )
    context = app_context(
        config,
        root / "migrations",
        cloud_credentials=CloudCredentials("libsql://database.turso.io", "token"),
        oidc_claims={
            "sub": "admin-subject",
            "email": "admin@example.invalid",
            "exp": int(datetime.now(UTC).timestamp()) + 600,
        },
    )
    assert context.actor.role is Role.VIEWER
    connection.close()


def test_cloud_context_rejects_missing_secrets_or_identity(tmp_path: Path) -> None:
    config = LocalAppConfig(
        RuntimeInfo("production", "cloud"),
        tmp_path / "unused.sqlite",
        "developer@example.invalid",
        "editor",
        True,
    )
    with pytest.raises(ValueError, match="credentials"):
        app_context(config, Path("migrations"))
    with pytest.raises(ValueError, match="OIDC"):
        app_context(
            config,
            Path("migrations"),
            cloud_credentials=CloudCredentials("libsql://database.turso.io", "token"),
        )


def test_hosted_cloud_context_uses_configured_shared_audit_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "hosted-cloud.sqlite",
            DatabaseBackend.FAKE_CLOUD,
            root / "migrations",
        )
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "email": "owner@example.com",
                "display_name": "owner",
                "role": Role.ADMIN,
                "is_active": True,
            }
        )
    captured: dict[str, object] = {}

    def cloud_repository(*args: object, **kwargs: object) -> SqlPlateReaderRepository:
        captured.update(kwargs)
        return repository

    monkeypatch.setattr(context_module, "_cached_cloud_repository", cloud_repository)
    config = LocalAppConfig(
        RuntimeInfo("production", "cloud"),
        tmp_path / "unused.sqlite",
        "developer@example.invalid",
        "editor",
        True,
        "hosted",
        "owner@example.com",
        "admin",
    )

    context = app_context(
        config,
        root / "migrations",
        cloud_credentials=CloudCredentials("libsql://database.turso.io", "token"),
    )

    assert context.actor.email == "owner@example.com"
    assert context.actor.role is Role.ADMIN
    stored = repository.user_by_email("owner@example.com")
    assert stored is not None
    assert stored["role"] == "admin"
    assert captured["hosted_user_email"] == "owner@example.com"
    assert captured["hosted_user_role"] == "admin"
    connection.close()


def test_cached_cloud_connection_initializes_hosted_identity_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "hosted-initialization.sqlite",
            DatabaseBackend.FAKE_CLOUD,
            root / "migrations",
        )
    )
    connection_count = 0

    def connect(_config: object) -> object:
        nonlocal connection_count
        connection_count += 1
        return connection

    monkeypatch.setattr(context_module, "connect_turso_database", connect)
    context_module._cached_cloud_repository.clear()
    try:
        first = context_module._cached_cloud_repository(
            "libsql://hosted-initialization.turso.io",
            str(root / "migrations"),
            hosted_user_email="owner@example.com",
            hosted_user_role="admin",
            _auth_token="token",
        )
        second = context_module._cached_cloud_repository(
            "libsql://hosted-initialization.turso.io",
            str(root / "migrations"),
            hosted_user_email="owner@example.com",
            hosted_user_role="admin",
            _auth_token="token",
        )

        assert first is second
        assert connection_count == 1
        stored = first.user_by_email("owner@example.com")
        assert stored is not None
        assert stored["role"] == "admin"
    finally:
        context_module._cached_cloud_repository.clear()
        connection.close()


def test_cloud_repository_reconnects_once_after_an_expired_hrana_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]

    class StaleConnection:
        def execute(self, _statement: str) -> None:
            raise ValueError("Hrana: api error: stream not found: stream-id")

    stale = type("Repository", (), {"connection": StaleConnection()})()
    fresh_connection = sqlite3.connect(":memory:", isolation_level=None)
    fresh = type("Repository", (), {"connection": fresh_connection})()

    class CachedFactory:
        def __init__(self) -> None:
            self.calls = 0
            self.clear_calls = 0

        def __call__(self, *_args: object, **_kwargs: object) -> object:
            self.calls += 1
            return stale if self.calls == 1 else fresh

        def clear(self) -> None:
            self.clear_calls += 1

    factory = CachedFactory()
    monkeypatch.setattr(context_module, "_cached_cloud_repository", factory)

    try:
        repository = context_module._healthy_cloud_repository(
            CloudCredentials("libsql://database.turso.io", "token"),
            root / "migrations",
            hosted_user_email="",
            hosted_user_role="",
        )
    finally:
        fresh_connection.close()

    assert repository is fresh
    assert factory.calls == 2
    assert factory.clear_calls == 1


def test_cloud_repository_does_not_retry_other_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]

    class BrokenConnection:
        def execute(self, _statement: str) -> None:
            raise ValueError("Turso authentication failed")

    broken = type("Repository", (), {"connection": BrokenConnection()})()

    class CachedFactory:
        def __init__(self) -> None:
            self.clear_calls = 0

        def __call__(self, *_args: object, **_kwargs: object) -> object:
            return broken

        def clear(self) -> None:
            self.clear_calls += 1

    factory = CachedFactory()
    monkeypatch.setattr(context_module, "_cached_cloud_repository", factory)

    with pytest.raises(ValueError, match="authentication"):
        context_module._healthy_cloud_repository(
            CloudCredentials("libsql://database.turso.io", "token"),
            root / "migrations",
            hosted_user_email="",
            hosted_user_role="",
        )

    assert factory.clear_calls == 0
