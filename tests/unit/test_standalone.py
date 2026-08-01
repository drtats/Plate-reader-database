from __future__ import annotations

import json
from pathlib import Path

import pytest

from plate_reader.standalone import (
    DESKTOP_CONFIG_VERSION,
    create_desktop_backup,
    default_user_data_directory,
    initialize_desktop_database,
    initialize_desktop_paths,
    main,
    restore_desktop_backup,
)


def test_platform_user_data_directories() -> None:
    home = Path("/users/tester")
    assert default_user_data_directory(platform="darwin", home=home) == (
        home / "Library" / "Application Support" / "Plate Reader Database"
    )
    assert default_user_data_directory(
        platform="win32",
        environment={"LOCALAPPDATA": "C:/Users/tester/AppData/Local"},
        home=home,
    ) == Path("C:/Users/tester/AppData/Local/Plate Reader Database")
    assert default_user_data_directory(
        platform="linux", environment={"XDG_DATA_HOME": "/data/tester"}, home=home
    ) == Path("/data/tester/plate-reader-database")
    assert default_user_data_directory(platform="linux", environment={}, home=home) == (
        home / ".local" / "share" / "plate-reader-database"
    )


def test_first_run_and_database_selection_are_persistent(tmp_path: Path) -> None:
    paths = initialize_desktop_paths(tmp_path / "data")
    assert paths.database_path == (tmp_path / "data" / "plate-reader.sqlite").resolve()
    assert paths.backup_directory.is_dir()
    config = json.loads(paths.config_path.read_text(encoding="utf-8"))
    assert config == {
        "database_path": str(paths.database_path),
        "version": DESKTOP_CONFIG_VERSION,
    }

    selected = initialize_desktop_paths(
        paths.data_directory,
        selected_database=tmp_path / "selected.sqlite",
    )
    reopened = initialize_desktop_paths(paths.data_directory)
    assert selected.database_path == reopened.database_path == (tmp_path / "selected.sqlite")


@pytest.mark.parametrize(
    "payload",
    (
        "not json",
        '{"version": 999, "database_path": "/tmp/data.sqlite"}',
        '{"version": 1, "database_path": ""}',
    ),
)
def test_invalid_desktop_configuration_is_rejected(tmp_path: Path, payload: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "desktop-config.json").write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="desktop"):
        initialize_desktop_paths(data)


def test_info_and_run_dry_run_do_not_start_streamlit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(("--data-dir", str(tmp_path), "info")) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["database_exists"] is False
    assert main(("--data-dir", str(tmp_path), "run", "--dry-run")) == 0
    assert json.loads(capsys.readouterr().out)["database_path"].endswith("plate-reader.sqlite")


def test_desktop_database_backup_and_safe_restore(tmp_path: Path) -> None:
    paths = initialize_desktop_paths(tmp_path / "data")
    initialize_desktop_database(paths)
    backup = create_desktop_backup(paths, tmp_path / "backup.sqlite")
    restored = restore_desktop_backup(paths, backup, tmp_path / "restored.sqlite")

    assert paths.database_path.is_file()
    assert backup.is_file()
    assert restored.database_path == (tmp_path / "restored.sqlite").resolve()
    assert restored.database_path.is_file()
    assert initialize_desktop_paths(paths.data_directory).database_path == restored.database_path
    with pytest.raises(ValueError, match="must differ"):
        restore_desktop_backup(restored, backup, restored.database_path)
