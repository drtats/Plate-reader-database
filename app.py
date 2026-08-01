"""Streamlit entry point for the local growth vertical slice."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from plate_reader import __version__
from plate_reader.runtime import load_local_app_config
from plate_reader.ui.context import app_context
from plate_reader.ui.mic_pages import (
    render_mic_library,
    render_mic_results_search,
    render_mic_wizard,
    render_mic_workspace,
)
from plate_reader.ui.pages import render_growth_wizard, render_run_library, render_workspace
from plate_reader.ui.portable_pages import render_portable_import

st.set_page_config(
    page_title="Plate Reader Database",
    page_icon="🧫",
    layout="wide",
)


def main() -> None:
    """Render the local application without putting SQL in the presentation layer."""

    st.title("Plate Reader Database")
    st.caption("Modular growth-curve and MIC data platform")

    try:
        root = Path(__file__).resolve().parent
        config = load_local_app_config(root)
        context = app_context(config, root / "migrations")
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

    st.sidebar.caption(f"Signed in for development as {context.actor.email}")
    if pending_navigation := st.session_state.pop("pending_navigation", None):
        st.session_state.navigation = pending_navigation
    navigation = st.sidebar.radio(
        "Navigation",
        (
            "Growth Run Library",
            "New Growth Run",
            "Growth Workspace",
            "MIC Plate Library",
            "New MIC Plate",
            "MIC Workspace",
            "MIC Results",
            "Import Portable Data",
        ),
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
        render_mic_library(context)
    elif navigation == "New MIC Plate":
        render_mic_wizard(
            context,
            allow_local_path=config.runtime.storage_mode in {"local", "fake-cloud"},
        )
    elif navigation == "MIC Workspace":
        render_mic_workspace(context, root / "migrations")
    elif navigation == "MIC Results":
        render_mic_results_search(context)
    else:
        render_portable_import(
            context,
            allow_local_path=config.runtime.storage_mode in {"local", "fake-cloud"},
        )


if __name__ == "__main__":
    main()
