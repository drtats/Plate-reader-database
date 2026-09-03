"""Separate metadata-first UI for multi-run Growth CSV exports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import AssayType, PlateId, SearchRuns
from plate_reader.application.ports.repositories import RunSummary
from plate_reader.application.services import (
    ExportGrowthTabularData,
    ExportGrowthTabularDataService,
    GrowthTabularExportBundle,
    SearchGrowthRunsService,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.option_controls import layout_custom_column_names
from plate_reader.ui.run_summary_table import run_summary_table


def render_growth_data_export(context: AppContext) -> None:
    """Select multiple Growth runs and prepare the two analysis CSV files."""

    st.header("Growth Data Export")
    st.markdown(
        "Export complete Growth runs in two files: one row per OD observation and a "
        "companion run/well metadata table. The observation file keeps **Raw OD**, "
        "**Background Mean OD**, and **Background Subtracted OD** as separate columns."
    )
    with st.form("growth-export-search"):
        text = st.text_input(
            "Search experiment, plate, project, strain, treatment, or medium",
            key="growth_export_search_text",
        )
        search = st.form_submit_button("Search runs")
    if search or "growth_export_search_results" not in st.session_state:
        try:
            st.session_state.growth_export_search_results = SearchGrowthRunsService(
                context.repository
            ).execute(SearchRuns(context.actor, text=text, limit=500))
            st.session_state.growth_export_custom_columns = layout_custom_column_names(
                context, AssayType.GROWTH
            )
        except Exception as error:
            st.error(f"Unable to search Growth runs: {error}")
            return
        st.session_state.growth_export_table_revision = (
            int(st.session_state.get("growth_export_table_revision", 0)) + 1
        )
        _clear_artifact()
    elif "growth_export_custom_columns" not in st.session_state:
        try:
            st.session_state.growth_export_custom_columns = layout_custom_column_names(
                context, AssayType.GROWTH
            )
        except Exception as error:
            st.error(f"Unable to load Growth layout columns: {error}")
            return

    results = cast(Sequence[RunSummary], st.session_state.growth_export_search_results)
    if not results:
        st.info("No Growth runs match this search.")
        return

    custom_columns = cast(tuple[str, ...], st.session_state.growth_export_custom_columns)
    table = _export_table(results, custom_columns)
    revision = int(st.session_state.get("growth_export_table_revision", 0))
    with st.form("growth-export-selection"):
        edited = st.data_editor(
            table,
            key=f"growth-export-table-{revision}",
            hide_index=True,
            width="stretch",
            disabled=[column for column in table.columns if column != "Select"],
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", default=False),
            },
        )
        prepare = st.form_submit_button("Prepare selected runs", type="primary")

    selected = _selected_plate_ids(edited)
    st.caption(
        f"Selected Growth runs: {len(selected)}. "
        "Raw observations load only when Prepare is pressed."
    )
    if prepare:
        try:
            bundle = ExportGrowthTabularDataService(context.repository).execute(
                ExportGrowthTabularData(context.actor, selected)
            )
        except Exception as error:
            _clear_artifact()
            st.error(f"Unable to prepare Growth CSV export: {error}")
        else:
            st.session_state.growth_tabular_export_bundle = bundle
            st.session_state.growth_tabular_export_plate_ids = tuple(map(str, selected))

    saved_ids = st.session_state.get("growth_tabular_export_plate_ids")
    if saved_ids != tuple(map(str, selected)):
        return
    saved = st.session_state.get("growth_tabular_export_bundle")
    if saved is None:
        return
    bundle = cast(GrowthTabularExportBundle, saved)
    left, middle, right = st.columns(3)
    left.metric("Runs", len(selected))
    middle.metric("OD observation rows", bundle.measurements.row_count)
    right.metric("Experiment metadata rows", bundle.metadata.row_count)
    for warning in bundle.warnings:
        st.warning(warning)
    downloads = st.columns(2)
    downloads[0].download_button(
        "Download growth_runs.csv",
        data=bundle.measurements.content,
        file_name=bundle.measurements.filename,
        mime="text/csv",
    )
    downloads[1].download_button(
        "Download growth_runs_metadata.csv",
        data=bundle.metadata.content,
        file_name=bundle.metadata.filename,
        mime="text/csv",
    )


def _export_table(
    results: Sequence[RunSummary], custom_columns: Sequence[str] = ()
) -> pd.DataFrame:
    """Build the export selector with the same metadata columns as the Library."""

    return run_summary_table(results, custom_columns)


def _selected_plate_ids(table: pd.DataFrame) -> tuple[PlateId, ...]:
    return tuple(PlateId(str(plate_id)) for plate_id in table.index[table["Select"]])


def _clear_artifact() -> None:
    st.session_state.pop("growth_tabular_export_bundle", None)
    st.session_state.pop("growth_tabular_export_plate_ids", None)
