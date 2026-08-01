"""Standalone launcher, first-run configuration, backup, and restore."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    backup_complete_database,
    connect_database,
    restore_complete_database,
)

DESKTOP_CONFIG_VERSION = 1
APPLICATION_DIRECTORY_NAME = "Plate Reader Database"


@dataclass(frozen=True, slots=True)
class DesktopPaths:
    data_directory: Path
    database_path: Path
    backup_directory: Path
    config_path: Path


@dataclass(frozen=True, slots=True)
class DesktopConfig:
    version: int
    database_path: str


def default_user_data_directory(
    *,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return a platform-native, user-writable data directory."""

    selected_platform = platform or sys.platform
    selected_environment = environment if environment is not None else os.environ
    selected_home = home if home is not None else Path.home()
    if selected_platform == "darwin":
        return selected_home / "Library" / "Application Support" / APPLICATION_DIRECTORY_NAME
    if selected_platform.startswith("win"):
        local_app_data = selected_environment.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else selected_home / "AppData" / "Local"
        return base / APPLICATION_DIRECTORY_NAME
    xdg_data_home = selected_environment.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else selected_home / ".local" / "share"
    return base / "plate-reader-database"


def initialize_desktop_paths(
    data_directory: Path | None = None,
    *,
    selected_database: Path | None = None,
) -> DesktopPaths:
    """Create/reuse first-run directories and persist the selected database."""

    data_directory = (data_directory or default_user_data_directory()).expanduser().resolve()
    data_directory.mkdir(parents=True, exist_ok=True)
    backup_directory = data_directory / "backups"
    backup_directory.mkdir(exist_ok=True)
    config_path = data_directory / "desktop-config.json"
    stored = _read_desktop_config(config_path)
    database_path = (
        selected_database.expanduser().resolve()
        if selected_database is not None
        else Path(stored.database_path).expanduser().resolve()
        if stored is not None
        else data_directory / "plate-reader.sqlite"
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = DesktopConfig(DESKTOP_CONFIG_VERSION, str(database_path))
    if stored != config:
        _write_desktop_config(config_path, config)
    return DesktopPaths(data_directory, database_path, backup_directory, config_path)


def resource_root() -> Path:
    """Locate bundled resources or the source checkout root."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(str(bundle_root))
    return Path(__file__).resolve().parents[2]


def create_desktop_backup(paths: DesktopPaths, destination: Path | None = None) -> Path:
    if not paths.database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {paths.database_path}")
    selected_destination = destination or paths.backup_directory / (
        f"plate-reader-{datetime.now(UTC):%Y%m%d-%H%M%S}.sqlite"
    )
    selected_destination = selected_destination.expanduser().resolve()
    connection = connect_database(
        DatabaseConfig(paths.database_path, DatabaseBackend.PYTURSO, resource_root() / "migrations")
    )
    try:
        backup_complete_database(
            connection,
            selected_destination,
            resource_root() / "migrations",
        )
    finally:
        connection.close()
    return selected_destination


def initialize_desktop_database(paths: DesktopPaths) -> None:
    """Create or migrate the selected local database without starting the UI."""

    connection = connect_database(
        DatabaseConfig(paths.database_path, DatabaseBackend.PYTURSO, resource_root() / "migrations")
    )
    connection.close()


def restore_desktop_backup(
    paths: DesktopPaths,
    backup: Path,
    destination: Path | None = None,
) -> DesktopPaths:
    """Restore into a new file and select it; never overwrite the active database."""

    backup = backup.expanduser().resolve()
    if not backup.is_file():
        raise FileNotFoundError(f"Backup does not exist: {backup}")
    selected_destination = destination or paths.data_directory / (
        f"plate-reader-restored-{datetime.now(UTC):%Y%m%d-%H%M%S}.sqlite"
    )
    selected_destination = selected_destination.expanduser().resolve()
    if selected_destination == paths.database_path:
        raise ValueError("Restore destination must differ from the active database")
    restore_complete_database(backup, selected_destination, resource_root() / "migrations")
    return initialize_desktop_paths(
        paths.data_directory,
        selected_database=selected_destination,
    )


def configure_standalone_environment(paths: DesktopPaths) -> None:
    os.environ.update(
        {
            "PLATE_READER_ENV": "development",
            "PLATE_READER_STORAGE_MODE": "local",
            "PLATE_READER_DATABASE_PATH": str(paths.database_path),
            "PLATE_READER_DEV_ROLE": "admin",
            "PLATE_READER_STANDALONE": "true",
        }
    )


def launch_streamlit(paths: DesktopPaths, *, port: int, open_browser: bool) -> int:
    initialize_desktop_database(paths)
    configure_standalone_environment(paths)
    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(resource_root() / "app.py"),
        "--server.port",
        str(port),
        "--server.headless",
        "false" if open_browser else "true",
        "--global.developmentMode",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    result = streamlit_cli.main()
    return int(result or 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    paths = initialize_desktop_paths(args.data_dir, selected_database=args.database)
    command = args.command or "run"
    if command == "info":
        print(json.dumps(_public_info(paths), indent=2, sort_keys=True))
        return 0
    if command == "init":
        initialize_desktop_database(paths)
        print(f"Local database ready: {paths.database_path}")
        return 0
    if command == "backup":
        created = create_desktop_backup(paths, args.destination)
        print(f"Verified backup created: {created}")
        return 0
    if command == "restore":
        restored = restore_desktop_backup(paths, args.backup, args.destination)
        print(f"Verified restore selected: {restored.database_path}")
        return 0
    if args.dry_run:
        print(json.dumps(_public_info(paths), indent=2, sort_keys=True))
        return 0
    return launch_streamlit(paths, port=args.port, open_browser=not args.no_browser)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Override the OS user-data directory")
    parser.add_argument("--database", type=Path, help="Select and remember a database file")
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="Start the local application")
    run.add_argument("--port", type=int, default=8501)
    run.add_argument("--no-browser", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="Validate setup without starting UI")
    backup = subparsers.add_parser("backup", help="Create a verified complete backup")
    backup.add_argument("destination", type=Path, nargs="?")
    restore = subparsers.add_parser("restore", help="Restore safely into a new selected database")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--destination", type=Path)
    subparsers.add_parser("info", help="Print non-secret desktop paths")
    subparsers.add_parser("init", help="Create or migrate the selected local database")
    parser.set_defaults(port=8501, no_browser=False, dry_run=False)
    return parser


def _public_info(paths: DesktopPaths) -> dict[str, object]:
    return {
        "config_version": DESKTOP_CONFIG_VERSION,
        "data_directory": str(paths.data_directory),
        "database_path": str(paths.database_path),
        "database_exists": paths.database_path.is_file(),
        "backup_directory": str(paths.backup_directory),
        "resource_root": str(resource_root()),
    }


def _read_desktop_config(path: Path) -> DesktopConfig | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid desktop configuration: {path}") from error
    if not isinstance(payload, dict) or payload.get("version") != DESKTOP_CONFIG_VERSION:
        raise ValueError(f"Unsupported desktop configuration: {path}")
    database_path = payload.get("database_path")
    if not isinstance(database_path, str) or not database_path.strip():
        raise ValueError(f"Invalid desktop database path: {path}")
    return DesktopConfig(DESKTOP_CONFIG_VERSION, database_path)


def _write_desktop_config(path: Path, config: DesktopConfig) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(config), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def console_main() -> NoReturn:
    raise SystemExit(main())


if __name__ == "__main__":
    console_main()
