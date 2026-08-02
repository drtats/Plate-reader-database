"""Cached Streamlit application context and development identity."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.application.services import OidcClaims, ResolveAuthenticatedActorService
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    TursoDatabaseConfig,
    connect_database,
    connect_turso_database,
)
from plate_reader.runtime import LocalAppConfig


@dataclass(slots=True)
class AppContext:
    repository: SqlPlateReaderRepository
    actor: Actor


@dataclass(frozen=True, slots=True)
class CloudCredentials:
    database_url: str
    auth_token: str


def app_context(
    config: LocalAppConfig,
    migrations: Path,
    *,
    cloud_credentials: CloudCredentials | None = None,
    oidc_claims: Mapping[str, object] | None = None,
) -> AppContext:
    if config.runtime.storage_mode == "cloud":
        if cloud_credentials is None:
            raise ValueError("Turso credentials are required in cloud mode")
        if config.cloud_identity_mode == "oidc" and oidc_claims is None:
            raise ValueError("An authenticated OIDC identity is required in cloud mode")
        repository = _cached_cloud_repository(
            cloud_credentials.database_url,
            str(migrations),
            hosted_user_email=(
                config.hosted_user_email if config.cloud_identity_mode == "hosted" else ""
            ),
            hosted_user_role=(
                config.hosted_user_role if config.cloud_identity_mode == "hosted" else ""
            ),
            _auth_token=cloud_credentials.auth_token,
        )
        if config.cloud_identity_mode == "hosted":
            actor = _hosted_actor(repository, config)
        else:
            assert oidc_claims is not None
            actor = ResolveAuthenticatedActorService(repository).execute(
                OidcClaims.from_mapping(oidc_claims, require_expiration=True)
            )
        if not config.writes_enabled:
            actor = Actor(actor.user_id, actor.email, Role.VIEWER)
        return AppContext(repository, actor)

    backend = (
        DatabaseBackend.PYTURSO
        if config.runtime.storage_mode == "local"
        else DatabaseBackend.FAKE_CLOUD
    )
    if config.runtime.storage_mode not in {"local", "fake-cloud"}:
        raise ValueError("Sync storage is not enabled; choose local, fake-cloud, or cloud")
    return _cached_context(
        str(config.database_path),
        backend,
        str(migrations),
        config.development_user_email,
        config.development_user_role,
        config.writes_enabled,
    )


def _hosted_actor(repository: SqlPlateReaderRepository, config: LocalAppConfig) -> Actor:
    """Resolve the one audit identity trusted behind a host-managed access gate."""

    email = config.hosted_user_email
    stored = repository.user_by_email(email)
    if stored is None or not bool(stored["is_active"]):
        raise ValueError("Hosted audit identity could not be initialized")
    return Actor(UserId(str(stored["user_id"])), email, Role(str(stored["role"])))


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


@st.cache_resource(show_spinner="Connecting to Turso Cloud…")
def _cached_cloud_repository(
    database_url: str,
    migrations_directory: str,
    *,
    hosted_user_email: str = "",
    hosted_user_role: str = "",
    _auth_token: str,
) -> SqlPlateReaderRepository:
    connection = connect_turso_database(
        TursoDatabaseConfig(database_url, _auth_token, Path(migrations_directory))
    )
    repository = SqlPlateReaderRepository(connection)
    if hosted_user_email:
        with repository.transaction():
            repository.upsert_user(
                {
                    "email": hosted_user_email,
                    "display_name": hosted_user_email.split("@", maxsplit=1)[0],
                    "role": hosted_user_role,
                    "is_active": True,
                }
            )
    return repository
