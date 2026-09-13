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
from plate_reader.application.services.growth_tabular_export import ExportCultivationSettings
from plate_reader.domain.growth.cultivation import (
    DEFAULT_CULTIVATION_PATTERN,
    LEGACY_CULTIVATION_PATTERN,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.option_controls import layout_custom_column_names
from plate_reader.ui.run_summary_table import run_summary_table

_RECOMMENDED_PATTERN = "Experiment number + well (recommended)"
_LABORATORY_PATTERN = "Original laboratory format"
_CUSTOM_PATTERN = "Custom pattern"
_SAVED_PATTERNS = "Use saved patterns"


def render_growth_data_export(context: AppContext) -> None:
    """Select multiple Growth runs and prepare the two analysis CSV files."""

    st.header("Growth Data Export")
    st.markdown(
        "Export complete Growth runs in two files: one row per OD observation and a "
        "companion cultivation metadata table linked by **Cultivation ID** / **Cultivation**. "
        "Generate cultivation IDs and cumulative replicate numbers for the selected runs. "
        "Saved identity components can be reused or overridden for this export. "
        "Strain, treatments, concentrations "
        "and units are separate fields. The observation file keeps **Raw OD**, "
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
    selected = _selected_plate_ids(edited)
    st.caption(
        f"Selected Growth runs: {len(selected)}. "
        "Raw observations load only when the preparation button is pressed."
    )

    st.subheader("Cultivation ID generation")
    assign_replicates = st.checkbox(
        "Generate cultivation IDs and cumulative replicate numbers",
        value=True,
        key="growth_export_assign_replicates",
    )
    choice = st.selectbox(
        "Cultivation ID pattern",
        (_RECOMMENDED_PATTERN, _LABORATORY_PATTERN, _CUSTOM_PATTERN, _SAVED_PATTERNS),
        key="growth_export_pattern_choice",
        disabled=not assign_replicates,
    )
    pattern: str | None
    if choice == _RECOMMENDED_PATTERN:
        pattern = DEFAULT_CULTIVATION_PATTERN
    elif choice == _LABORATORY_PATTERN:
        pattern = LEGACY_CULTIVATION_PATTERN
    elif choice == _CUSTOM_PATTERN:
        pattern = st.text_input(
            "Custom cultivation ID pattern",
            value=DEFAULT_CULTIVATION_PATTERN,
            key="growth_export_custom_pattern",
            disabled=not assign_replicates,
            help="Use {team}, {strain}, {system}, {run}, {experiment}, {well}, and {replicate}.",
        )
    else:
        pattern = None
    if pattern is None:
        st.caption("Each well uses its saved pattern, then its run pattern; unsaved patterns use:")
        st.code(DEFAULT_CULTIVATION_PATTERN, language=None)
    else:
        st.code(pattern, language=None)
    st.caption(
        "Pattern tokens: {team}, {strain}, {system}, {run}, {experiment}, {well}, "
        "{replicate}. The R suffix requires {replicate}. Missing experiment numbers "
        "are assigned chronologically across the Growth library, starting at 001. "
        "Saved numbers stay fixed."
    )
    team_code = st.text_input(
        "Team code for this export (optional)",
        key="growth_export_team_code",
        disabled=not assign_replicates,
        help="Leave blank to use each well or run's saved team code.",
    )
    system_code = ""
    if choice in (_LABORATORY_PATTERN, _CUSTOM_PATTERN, _SAVED_PATTERNS):
        system_code = st.text_input(
            "Cultivation system code for this export (optional)",
            key="growth_export_system_code",
            disabled=not assign_replicates,
            help="Used by patterns with {system}; leave blank to use a saved value.",
        )
    extra_fields_text = st.text_input(
        "Additional condition fields for this export (comma-separated)",
        key="growth_export_condition_fields",
        disabled=not assign_replicates,
        help="Additional well custom column names; saved matching fields are also included.",
    )
    condition_fields = (
        tuple(sorted({name.strip() for name in extra_fields_text.split(",") if name.strip()}))
        if assign_replicates
        else ()
    )
    if assign_replicates:
        st.caption(
            "Matching wells receive R1, R2, … across the selected runs in experiment-date "
            "and well order. Each condition group starts at R1. The primary CSV's "
            "Replicate column is this cumulative R number; Local replicate keeps the "
            "original Layout value. Saved IDs and metadata remain unchanged. "
            "Saved study/group settings limit which wells count together. "
            "Concentration units are normalized to u (for example, ug/mL)."
        )
    settings = (
        ExportCultivationSettings(
            pattern=pattern,
            team_code=team_code.strip(),
            system_code=system_code.strip(),
        )
        if assign_replicates
        else None
    )
    signature = (
        tuple(map(str, selected)),
        assign_replicates,
        condition_fields,
        choice if assign_replicates else None,
        settings,
    )
    prepare = st.button(
        "Generate cultivation IDs and prepare export"
        if assign_replicates
        else "Prepare selected runs",
        type="primary",
    )
    if prepare:
        try:
            bundle = ExportGrowthTabularDataService(context.repository).execute(
                ExportGrowthTabularData(
                    context.actor,
                    selected,
                    assign_selected_replicates=assign_replicates,
                    condition_fields=condition_fields,
                    cultivation_settings=settings,
                )
            )
        except Exception as error:
            _clear_artifact()
            st.error(f"Unable to prepare Growth CSV export: {error}")
        else:
            st.session_state.growth_tabular_export_bundle = bundle
            st.session_state.growth_tabular_export_plate_ids = tuple(map(str, selected))
            st.session_state.growth_tabular_export_signature = signature

    if st.session_state.get("growth_tabular_export_signature") != signature:
        return
    saved = st.session_state.get("growth_tabular_export_bundle")
    if saved is None:
        return
    bundle = cast(GrowthTabularExportBundle, saved)
    left, middle, right = st.columns(3)
    left.metric("Runs", len(selected))
    middle.metric("OD observation rows", bundle.measurements.row_count)
    right.metric("Cultivation metadata rows", bundle.metadata.row_count)
    for warning in bundle.warnings:
        st.warning(warning)
    if bundle.replicate_preview:
        st.subheader("Cultivation IDs and replicates for this export")
        st.dataframe(pd.DataFrame(bundle.replicate_preview), hide_index=True, width="stretch")
        if bundle.effective_condition_fields:
            st.caption(
                "Additional matching fields: " + ", ".join(bundle.effective_condition_fields)
            )
    downloads = st.columns(2)
    downloads[0].download_button(
        f"Download {bundle.measurements.filename}",
        data=bundle.measurements.content,
        file_name=bundle.measurements.filename,
        mime="text/csv",
        key="growth-tabular-measurements-download",
        on_click="ignore",
    )
    downloads[1].download_button(
        f"Download {bundle.metadata.filename}",
        data=bundle.metadata.content,
        file_name=bundle.metadata.filename,
        mime="text/csv",
        key="growth-tabular-metadata-download",
        on_click="ignore",
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
    st.session_state.pop("growth_tabular_export_signature", None)
