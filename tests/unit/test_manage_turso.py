from __future__ import annotations

from pathlib import Path

import pytest
from scripts.manage_turso import _bootstrap_admin, _config_from_environment

from plate_reader.infrastructure.database import DatabaseBackend, DatabaseConfig, connect_database


def test_remote_cli_configuration_uses_environment_without_cli_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://database.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "secret-token")
    config = _config_from_environment(Path("migrations"))
    assert config.database_url == "libsql://database.turso.io"
    assert config.auth_token == "secret-token"

    monkeypatch.delenv("TURSO_AUTH_TOKEN")
    with pytest.raises(SystemExit, match="must be set"):
        _config_from_environment(Path("migrations"))


def test_bootstrap_admin_is_one_time_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "bootstrap.sqlite", DatabaseBackend.FAKE_CLOUD, root / "migrations"
        )
    )
    try:
        _bootstrap_admin(connection, " Scientist@Example.Invalid ", " Lab Admin ")
        row = connection.execute(
            "SELECT email, display_name, role, is_active FROM users"
        ).fetchone()
        assert row == ("scientist@example.invalid", "Lab Admin", "admin", 1)
        assert "scientist@example.invalid" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="disabled"):
            _bootstrap_admin(connection, "other@example.invalid", "Other")
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("email", "display_name"),
    (("not-an-email", "Admin"), ("admin@example.invalid", "  ")),
)
def test_bootstrap_admin_validates_identity(email: str, display_name: str, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(tmp_path / "invalid.sqlite", DatabaseBackend.FAKE_CLOUD, root / "migrations")
    )
    try:
        with pytest.raises(SystemExit, match="valid email"):
            _bootstrap_admin(connection, email, display_name)
    finally:
        connection.close()
