"""Cached Streamlit application context and development identity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.runtime import LocalAppConfig


@dataclass(slots=True)
class AppContext:
    repository: SqlPlateReaderRepository
    actor: Actor


def app_context(config: LocalAppConfig, migrations: Path) -> AppContext:
    backend = (
        DatabaseBackend.PYTURSO
        if config.runtime.storage_mode == "local"
        else DatabaseBackend.FAKE_CLOUD
    )
    if config.runtime.storage_mode not in {"local", "fake-cloud"}:
        raise ValueError("Cloud and sync storage begin in Phase 5")
    return _cached_context(
        str(config.database_path),
        backend,
        str(migrations),
        config.development_user_email,
        config.development_user_role,
        config.writes_enabled,
    )


@st.cache_resource(show_spinner="Opening the plate-reader database…")
def _cached_context(
    database_path: str,
    backend: DatabaseBackend,
    migrations_directory: str,
    email: str,
    configured_role: str,
    writes_enabled: bool,
) -> AppContext:
    connection = connect_database(
        DatabaseConfig(Path(database_path), backend, Path(migrations_directory))
    )
    repository = SqlPlateReaderRepository(connection)
    user_id = f"dev-{hashlib.sha256(email.encode()).hexdigest()[:16]}"
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": user_id,
                "email": email,
                "display_name": email.split("@", maxsplit=1)[0],
                "role": Role(configured_role),
                "is_active": True,
            }
        )
    actor_role = Role(configured_role) if writes_enabled else Role.VIEWER
    return AppContext(repository, Actor(UserId(user_id), email, actor_role))
