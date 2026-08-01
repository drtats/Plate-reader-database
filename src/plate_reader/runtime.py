"""Non-secret runtime information for startup diagnostics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

EnvironmentName = Literal["development", "test", "production"]
StorageMode = Literal["local", "fake-cloud", "cloud", "sync"]


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    """Validated non-secret runtime settings safe to display in the UI."""

    environment: EnvironmentName
    storage_mode: StorageMode


@dataclass(frozen=True, slots=True)
class LocalAppConfig:
    runtime: RuntimeInfo
    database_path: Path
    development_user_email: str
    development_user_role: str
    writes_enabled: bool


def _read_choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


def load_runtime_info() -> RuntimeInfo:
    """Load and validate display-safe runtime settings from the environment."""

    environment = cast(
        EnvironmentName,
        _read_choice(
            "PLATE_READER_ENV",
            "development",
            {"development", "test", "production"},
        ),
    )
    storage_mode = cast(
        StorageMode,
        _read_choice(
            "PLATE_READER_STORAGE_MODE",
            "fake-cloud",
            {"local", "fake-cloud", "cloud", "sync"},
        ),
    )

    if environment == "production" and storage_mode == "fake-cloud":
        raise ValueError("fake-cloud storage cannot run in production")

    return RuntimeInfo(environment=environment, storage_mode=storage_mode)


def load_local_app_config(project_root: Path) -> LocalAppConfig:
    runtime = load_runtime_info()
    configured_path = os.getenv("PLATE_READER_DATABASE_PATH", ".data/plate-reader.sqlite").strip()
    if not configured_path:
        raise ValueError("PLATE_READER_DATABASE_PATH cannot be empty")
    database_path = Path(configured_path).expanduser()
    if not database_path.is_absolute():
        database_path = project_root / database_path
    development_user_email = (
        os.getenv("PLATE_READER_DEV_USER", "developer@example.invalid").strip().casefold()
    )
    if "@" not in development_user_email:
        raise ValueError("PLATE_READER_DEV_USER must be an email address")
    development_user_role = _read_choice(
        "PLATE_READER_DEV_ROLE", "editor", {"viewer", "editor", "admin"}
    )
    writes_enabled_text = os.getenv("PLATE_READER_WRITES_ENABLED", "true").strip().casefold()
    if writes_enabled_text not in {"true", "false"}:
        raise ValueError("PLATE_READER_WRITES_ENABLED must be true or false")
    return LocalAppConfig(
        runtime,
        database_path,
        development_user_email,
        development_user_role,
        writes_enabled_text == "true",
    )
