"""Streamlit entry point for the local growth vertical slice."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from plate_reader import __version__
from plate_reader.application.services import AuthenticationError
from plate_reader.arrow_runtime import configure_arrow_memory_pool
from plate_reader.runtime import load_local_app_config
from plate_reader.ui.cloud import load_cloud_credentials, oidc_provider
from plate_reader.ui.context import app_context
from plate_reader.ui.pages import render_growth_wizard, render_run_library, render_workspace
from plate_reader.ui.portable_pages import render_portable_import
from plate_reader.ui.theme import render_theme_control

configure_arrow_memory_pool()

st.set_page_config(
    page_title="Plate Reader Database",
    page_icon="🧫",
    layout="wide",
)


def main() -> None:
    """Render the local application without putting SQL in the presentation layer."""

    render_theme_control()
    st.title("Plate Reader Database")
    st.caption("Growth-curve data platform")

    try:
        root = Path(__file__).resolve().parent
        config = load_local_app_config(root)
        if config.runtime.storage_mode == "cloud" and config.cloud_identity_mode == "oidc":
            if not getattr(st.user, "is_logged_in", False):
                st.info("Sign in with the laboratory identity provider to continue.")
                if st.button("Sign in"):
                    st.login(oidc_provider())
                st.stop()
            context = app_context(
                config,
                root / "migrations",
                cloud_credentials=load_cloud_credentials(),
                oidc_claims=st.user.to_dict(),
            )
        elif config.runtime.storage_mode == "cloud":
            context = app_context(
                config,
                root / "migrations",
                cloud_credentials=load_cloud_credentials(),
            )
        else:
            context = app_context(config, root / "migrations")
    except AuthenticationError:
        st.error("This signed-in account is not authorized to use the application.")
        if st.button("Sign out and use another account"):
            st.logout()
        st.stop()
    except ValueError as exc:
        st.error(f"Configuration error: {exc}")
        st.stop()

    left, middle, right = st.columns(3)
    left.metric("Application version", __version__)
    middle.metric("Environment", config.runtime.environment)
    right.metric("Storage mode", config.runtime.storage_mode)

    if config.runtime.storage_mode == "fake-cloud":
        st.info(
            "Fake cloud mode is active. It uses no Turso credentials and does not "
            "verify real network behavior."
        )
    if not config.writes_enabled:
        st.warning("Read-only rollback mode is active. Write services are disabled.")

    if config.runtime.storage_mode == "cloud":
        if config.cloud_identity_mode == "hosted":
            st.sidebar.caption(f"Hosted audit identity: {context.actor.email}")
        else:
            st.sidebar.caption(f"Signed in as {context.actor.email}")
            if st.sidebar.button("Sign out"):
                st.logout()
    else:
        st.sidebar.caption(f"Signed in for development as {context.actor.email}")
    navigation_options = [
        "Growth Run Library",
        "New Growth Run",
        "Growth Workspace",
    ]
    if (
        config.runtime.environment == "test"
        and os.environ.get("PLATE_READER_TEST_ENABLE_MIC_UI") == "1"
    ):
        navigation_options.extend(
            ("MIC Plate Library", "New MIC Plate", "MIC Workspace", "MIC Results")
        )
    navigation_options.append("Import Portable Data")
    if (
        pending_navigation := st.session_state.pop("pending_navigation", None)
    ) and pending_navigation in navigation_options:
        st.session_state.navigation = pending_navigation
    if st.session_state.get("navigation") not in navigation_options:
        st.session_state.pop("navigation", None)
    navigation = st.sidebar.radio(
        "Navigation",
        navigation_options,
        key="navigation",
    )
    if navigation == "Growth Run Library":
        render_run_library(context)
    elif navigation == "New Growth Run":
        render_growth_wizard(
            context,
            allow_local_path=config.runtime.storage_mode in {"local", "fake-cloud"},
        )
    elif navigation == "Growth Workspace":
        render_workspace(context, root / "migrations")
    elif navigation == "MIC Plate Library":
        from plate_reader.ui.mic_pages import render_mic_library

        render_mic_library(context)
    elif navigation == "New MIC Plate":
        from plate_reader.ui.mic_pages import render_mic_wizard

        render_mic_wizard(
            context,
            allow_local_path=config.runtime.storage_mode in {"local", "fake-cloud"},
        )
    elif navigation == "MIC Workspace":
        from plate_reader.ui.mic_pages import render_mic_workspace

        render_mic_workspace(context, root / "migrations")
    elif navigation == "MIC Results":
        from plate_reader.ui.mic_pages import render_mic_results_search

        render_mic_results_search(context)
    else:
        render_portable_import(
            context,
            allow_local_path=config.runtime.storage_mode in {"local", "fake-cloud"},
        )


if __name__ == "__main__":
    main()
