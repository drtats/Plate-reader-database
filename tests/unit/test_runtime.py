"""Unit tests for non-secret runtime configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from plate_reader.runtime import (
    LocalAppConfig,
    RuntimeInfo,
    load_local_app_config,
    load_runtime_info,
)


def test_defaults_to_development_fake_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLATE_READER_ENV", raising=False)
    monkeypatch.delenv("PLATE_READER_STORAGE_MODE", raising=False)

    assert load_runtime_info() == RuntimeInfo(
        environment="development",
        storage_mode="fake-cloud",
    )


def test_accepts_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "development")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "local")

    assert load_runtime_info() == RuntimeInfo(
        environment="development",
        storage_mode="local",
    )


def test_rejects_unknown_storage_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "mystery")

    with pytest.raises(ValueError, match="PLATE_READER_STORAGE_MODE"):
        load_runtime_info()


def test_rejects_fake_cloud_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "production")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")

    with pytest.raises(ValueError, match="cannot run in production"):
        load_runtime_info()


def test_local_app_configuration_resolves_relative_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", "state/test.sqlite")
    monkeypatch.setenv("PLATE_READER_DEV_USER", "Scientist@Example.Invalid")

    assert load_local_app_config(tmp_path) == LocalAppConfig(
        RuntimeInfo("development", "fake-cloud"),
        tmp_path / "state/test.sqlite",
        "scientist@example.invalid",
        "editor",
        True,
    )


def test_local_app_configuration_rejects_empty_path_and_invalid_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", "")
    with pytest.raises(ValueError, match="DATABASE_PATH"):
        load_local_app_config(tmp_path)

    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", "test.sqlite")
    monkeypatch.setenv("PLATE_READER_DEV_USER", "not-an-email")
    with pytest.raises(ValueError, match="DEV_USER"):
        load_local_app_config(tmp_path)


def test_read_only_rollback_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLATE_READER_WRITES_ENABLED", "false")
    assert load_local_app_config(tmp_path).writes_enabled is False

    monkeypatch.setenv("PLATE_READER_WRITES_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="WRITES_ENABLED"):
        load_local_app_config(tmp_path)


def test_local_development_role_is_validated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_DEV_ROLE", "admin")
    assert load_local_app_config(tmp_path).development_user_role == "admin"

    monkeypatch.setenv("PLATE_READER_DEV_ROLE", "owner")
    with pytest.raises(ValueError, match="DEV_ROLE"):
        load_local_app_config(tmp_path)


def test_hosted_cloud_identity_requires_audit_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "production")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "cloud")
    monkeypatch.setenv("PLATE_READER_CLOUD_IDENTITY_MODE", "hosted")

    with pytest.raises(ValueError, match="HOSTED_USER_EMAIL"):
        load_local_app_config(tmp_path)

    monkeypatch.setenv("PLATE_READER_HOSTED_USER_EMAIL", "Owner@Example.com")
    monkeypatch.setenv("PLATE_READER_HOSTED_USER_ROLE", "editor")
    config = load_local_app_config(tmp_path)

    assert config.cloud_identity_mode == "hosted"
    assert config.hosted_user_email == "owner@example.com"
    assert config.hosted_user_role == "editor"


def test_cloud_identity_mode_is_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLATE_READER_CLOUD_IDENTITY_MODE", "password")
    with pytest.raises(ValueError, match="CLOUD_IDENTITY_MODE"):
        load_local_app_config(tmp_path)
