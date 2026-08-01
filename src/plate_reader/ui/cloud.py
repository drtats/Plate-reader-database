"""Secret loading and login helpers for the hosted Streamlit adapter."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

import streamlit as st

from plate_reader.ui.context import CloudCredentials

_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def load_cloud_credentials(
    secret_values: Mapping[str, object] | None = None,
) -> CloudCredentials:
    """Read credentials from environment first, then Streamlit secret storage."""

    values = secret_values if secret_values is not None else _streamlit_secrets()
    database_url = _secret("TURSO_DATABASE_URL", values)
    auth_token = _secret("TURSO_AUTH_TOKEN", values)
    return CloudCredentials(database_url, auth_token)


def oidc_provider() -> str:
    provider = os.getenv("PLATE_READER_OIDC_PROVIDER", "google").strip().casefold()
    if not _PROVIDER_NAME.fullmatch(provider):
        raise ValueError(
            "PLATE_READER_OIDC_PROVIDER must start with a letter and contain only "
            "lowercase letters, numbers, or hyphens"
        )
    return provider


def _streamlit_secrets() -> Mapping[str, object]:
    try:
        return st.secrets.to_dict()
    except (FileNotFoundError, AttributeError):
        return {}


def _secret(name: str, values: Mapping[str, object]) -> str:
    value = os.getenv(name)
    if value is None:
        stored = values.get(name)
        value = stored if isinstance(stored, str) else None
    if value is None or not value.strip():
        raise ValueError(f"{name} must be configured in host secret storage")
    return value.strip()
