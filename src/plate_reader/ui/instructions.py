"""In-app guide for the growth-curve workflow and hosted data handling."""

from __future__ import annotations

import streamlit as st


def render_instructions() -> None:
    st.header("Instructions")
    st.caption("A short guide to importing, reviewing, plotting, exporting, and storing runs.")

    st.subheader("Data flow")
    st.markdown("**CSV → Streamlit app → Turso database → workspace → plot or export**")
    st.write(
        "Turso stores your data permanently. Streamlit Cloud runs the interface and "
        "analysis temporarily: it retrieves a selected run from Turso, prepares plots or "
        "downloads in memory, sends the result to your browser, then discards that working copy."
    )

    with st.expander("1. Import a growth run", expanded=True):
        st.markdown(
            "1. Open **New Growth Run** and choose a CSV file.\n"
            "2. Validate the detected wells, timepoints, and measurement count.\n"
            "3. Enter experiment metadata.\n"
            "4. Review the 96-well plate layout or edit the full well table.\n"
            "5. Commit the run, then open its workspace."
        )
        st.info(
            "A typical 96-well, one-channel run is stored as a compact compressed series. "
            "The app expands it only while you are viewing, plotting, or exporting it."
        )

    with st.expander("2. Review and edit a run"):
        st.markdown(
            "Use **Growth Workspace** to update metadata, edit the 96-well layout, define "
            "blank/background wells, generate display names, and choose wells for plotting. "
            "Changes to metadata and layout are recorded in the activity log."
        )
        st.warning(
            "Raw plate-reader values are immutable after import. Correct a layout or metadata "
            "mistake in the workspace; do not expect a metadata save to rewrite measurements."
        )

    with st.expander("3. Plot and export"):
        st.markdown(
            "- Choose wells in the plate view or full-well table.\n"
            "- Use display names or a combined label built from well categories.\n"
            "- Export a long-format database-style CSV when you need all fields.\n"
            "- Export a wide plot-data CSV when you want time in the first column and one "
            "display-name column per selected curve."
        )

    with st.expander("4. Duplicate imports"):
        st.write(
            "The app blocks an exact repeat of the same CSV content using its SHA-256 file hash. "
            "It reopens the existing run instead of creating another copy."
        )
        st.write(
            "A file with changed bytes—even a re-export with minor formatting changes—is treated "
            "as a new run. This is deliberate: similar files can be genuine biological repeats. "
            "Review the experiment date, plate name, and source before committing."
        )

    with st.expander("5. Turso cleanup and replacement"):
        st.write(
            "Archiving or hiding a run does not reclaim database storage. If a Turso database is "
            "only test data, create a new empty database, update the two Streamlit secrets, verify "
            "the app, then delete the old database in Turso."
        )
        st.code(
            'TURSO_DATABASE_URL = "libsql://YOUR-DATABASE.turso.io"\n'
            'TURSO_AUTH_TOKEN = "YOUR_DATABASE_TOKEN"',
            language="toml",
        )
        st.caption("Never paste a real Turso token into a CSV, notebook, Git commit, or chat.")
