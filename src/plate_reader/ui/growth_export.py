"""Separate metadata-first UI for multi-run Growth CSV exports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import AssayType, PlateId, Role, SearchRuns
from plate_reader.application.ports.repositories import RunSummary
from plate_reader.application.services import (
    ExportGrowthTabularData,
    ExportGrowthTabularDataService,
    GrowthTabularExportBundle,
    SearchGrowthRunsService,
)
from plate_reader.application.services.growth_cultivation_registry import (
    PreviewGrowthCultivationRegistryService,
    RegistryPreview,
    SaveGrowthCultivationRegistryService,
)
from plate_reader.application.services.growth_tabular_export import ExportCultivationSettings
from plate_reader.domain.growth.cultivation import (
    DEFAULT_CULTIVATION_PATTERN,
    LEGACY_CULTIVATION_PATTERN,
)
from plate_reader.domain.growth.cultivation_registry import RegistrySettings
from plate_reader.ui.context import AppContext
from plate_reader.ui.option_controls import layout_custom_column_names
from plate_reader.ui.run_summary_table import run_summary_table

_RECOMMENDED_PATTERN = "Experiment number + well (recommended)"
_LABORATORY_PATTERN = "Original laboratory format"
_CUSTOM_PATTERN = "Custom pattern"
_SAVED_PATTERNS = "Use saved patterns"
_EXPORT_IDENTITY_VERSION = "export_run_v1"
_REGISTRY_IDENTITY_VERSION = "plate_condition_v1"
_SAVED_WORKFLOW = "Saved experiment + condition IDs (recommended)"
_LEGACY_WORKFLOW = "Legacy export patterns"
_CONCENTRATION_OPTIONS = (
    "2 significant figures (recommended)",
    "Exact values",
    "3 significant figures",
    "4 significant figures",
)
_CONCENTRATION_PRECISION = {
    "2 significant figures (recommended)": 2,
    "Exact values": None,
    "3 significant figures": 3,
    "4 significant figures": 4,
}


def render_growth_data_export(context: AppContext) -> None:
    """Select multiple Growth runs and prepare the two analysis CSV files."""

    st.header("Growth Data Export")
    st.markdown(
        "Export complete Growth runs in two files: one row per OD observation and a "
        "companion cultivation metadata table linked by **Cultivation ID** / **Cultivation**. "
        "Experiment and condition IDs can be previewed and saved before export. "
        "Each well has a stable internal ID and a readable local ID; the external ID "
        "identifies its experiment, condition group, and technical replicate. "
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
        _clear_registry_preview()
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
    workflow = st.selectbox(
        "Cultivation workflow",
        (_SAVED_WORKFLOW, _LEGACY_WORKFLOW),
        key="growth_export_workflow",
    )
    if st.session_state.get("growth_export_active_workflow") != workflow:
        _clear_artifact()
        _clear_registry_preview()
        st.session_state.growth_export_active_workflow = workflow
    if workflow == _SAVED_WORKFLOW:
        _render_saved_registry_workflow(context, selected)
        return
    _clear_registry_preview()

    st.subheader("Legacy cultivation ID generation for this export")
    assign_replicates = st.checkbox(
        "Generate cultivation IDs and replicate numbers within each run",
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
    concentration_matching = st.selectbox(
        "Concentration matching",
        _CONCENTRATION_OPTIONS,
        key="growth_export_concentration_matching",
        disabled=not assign_replicates,
        help="Applies to concentration doses when grouping wells within each run for R numbers.",
    )
    concentration_significant_figures = (
        _CONCENTRATION_PRECISION[concentration_matching] if assign_replicates else None
    )
    if assign_replicates:
        st.caption(
            "Matching wells receive R1, R2, … in well order within each run. Each run "
            "starts at R1; its experiment number distinguishes it from other runs. "
            "The primary CSV's Replicate column is this within-run R number; "
            "Local replicate keeps the original Layout value. Saved IDs and metadata "
            "remain unchanged. Saved study/group settings still separate condition groups. "
            "Concentration units are normalized to u (for example, ug/mL)."
        )
        st.caption(
            "Only concentration doses use the selected matching precision: 0.1875 and "
            "0.19 both match as 0.19 at 2 significant figures. Other condition fields "
            "must still match. Original concentrations remain unchanged in the export; "
            "Matching concentration columns show the grouping values."
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
        _EXPORT_IDENTITY_VERSION,
        tuple(map(str, selected)),
        assign_replicates,
        condition_fields,
        concentration_significant_figures,
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
                    concentration_significant_figures=concentration_significant_figures,
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
    _render_prepared_bundle(cast(GrowthTabularExportBundle, saved), selected, legacy=True)


def _render_saved_registry_workflow(context: AppContext, selected: tuple[PlateId, ...]) -> None:
    """Preview and explicitly save persistent IDs before exporting saved identities."""

    from plate_reader.domain.growth.cultivation_registry import PLATE_CONDITION_PATTERN

    st.subheader("Saved experiment + condition cultivation IDs")
    st.markdown(
        "Each experiment (one plate) gets a number and each distinct condition gets a "
        "cultivation group number. **R** is the technical replicate within that group; "
        "the group itself is the biological replicate group. The experiment and group numbers "
        "remain stable after saving."
    )
    st.code(PLATE_CONDITION_PATTERN.replace("{plate}", "{experiment}"), language=None)
    st.caption(
        "For example, ST-EXP-MG1655-MP96A0101R1 contains experiment 01, condition 01, and "
        "technical replicate R1. Every well also keeps its internal well UUID and a "
        "local ID such as EXP01-A01. Preview reads metadata only."
    )
    team_code = st.text_input(
        "Team code (optional)",
        key="growth_registry_team_code",
        help="Leave blank to use the saved team code for each run.",
    )
    system_code = st.text_input(
        "Cultivation system code (optional)",
        key="growth_registry_system_code",
        help="Leave blank to use the saved system code for each run.",
    )
    concentration_matching = st.selectbox(
        "Concentration matching",
        _CONCENTRATION_OPTIONS,
        key="growth_registry_concentration_matching",
        help="Only condition-group doses use this precision; saved doses stay intact.",
    )
    extra_fields_text = st.text_input(
        "Additional condition fields (comma-separated)",
        key="growth_registry_condition_fields",
        help="Additional well custom column names to distinguish condition groups.",
    )
    condition_fields = tuple(
        sorted({name.strip() for name in extra_fields_text.split(",") if name.strip()})
    )
    settings = RegistrySettings(
        team_code=team_code.strip(),
        system_code=system_code.strip(),
        concentration_significant_figures=_CONCENTRATION_PRECISION[concentration_matching],
        condition_fields=condition_fields,
    )
    signature = (_REGISTRY_IDENTITY_VERSION, tuple(map(str, selected)), settings)
    if (
        "growth_registry_signature" in st.session_state
        and st.session_state.growth_registry_signature != signature
    ):
        _clear_registry_preview()
    if (
        "growth_tabular_export_signature" in st.session_state
        and st.session_state.growth_tabular_export_signature != signature
    ):
        _clear_artifact()
    st.caption(
        "At 2 significant figures, doses 0.1875 and 0.19 match as 0.19. Strain, medium, "
        "treatments, units, and the other selected conditions must still match. "
        "Save updates cultivation metadata only; raw measurements stay unchanged."
    )

    if st.button("Preview cultivation IDs"):
        _clear_artifact()
        try:
            prepared_preview = PreviewGrowthCultivationRegistryService(context.repository).execute(
                context.actor, selected, settings
            )
        except Exception as error:
            _clear_registry_preview()
            st.error(f"Unable to preview cultivation IDs: {error}")
        else:
            st.session_state.growth_registry_preview = prepared_preview
            st.session_state.growth_registry_signature = signature

    saved_preview = st.session_state.get("growth_registry_preview")
    active_preview: RegistryPreview | None = (
        cast(RegistryPreview, saved_preview)
        if st.session_state.get("growth_registry_signature") == signature
        and saved_preview is not None
        else None
    )
    if active_preview is not None:
        _render_registry_preview(active_preview)
    can_save = context.actor.role in (Role.EDITOR, Role.ADMIN)
    if not can_save:
        st.caption(
            "An editor or admin can save these IDs; viewers can preview and export saved IDs."
        )
    if st.button("Save cultivation IDs", disabled=active_preview is None or not can_save):
        assert active_preview is not None
        try:
            changed = SaveGrowthCultivationRegistryService(context.repository).execute(
                context.actor, active_preview
            )
        except Exception as error:
            _clear_registry_preview()
            st.error(f"Unable to save cultivation IDs: {error}")
        else:
            _clear_artifact()
            _clear_registry_preview()
            if changed:
                st.success(
                    f"Saved cultivation IDs for {len(changed)} run(s). "
                    "Preview again to review saved IDs."
                )
            else:
                st.success("Cultivation IDs are already saved. Preview again to review them.")
        return

    if st.button("Prepare selected runs", type="primary"):
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
            st.session_state.growth_tabular_export_signature = signature
    if st.session_state.get("growth_tabular_export_signature") != signature:
        return
    saved = st.session_state.get("growth_tabular_export_bundle")
    if saved is not None:
        _render_prepared_bundle(cast(GrowthTabularExportBundle, saved), selected, legacy=False)


def _render_registry_preview(preview: RegistryPreview) -> None:
    """Show metadata-only plate plans and per-well identities before a write."""

    st.subheader("Cultivation ID preview")
    plate_rows = [
        {
            "Run ID": plan.plate_id,
            "Experiment number": plan.plate_number,
            "Cultivation experiment range": plan.registry.get("CultivationExperiment", ""),
            "Wells": len(plan.assignments),
        }
        for plan in preview.plates
    ]
    st.dataframe(pd.DataFrame(plate_rows), hide_index=True, width="stretch")
    assignments = [
        {
            "Run ID": plan.plate_id,
            "Well": assignment.get("Well", ""),
            "Experiment number": assignment.get("CultivationPlateNumber", plan.plate_number),
            "Condition group": assignment.get("BiologicalReplicateGroup", ""),
            "Technical replicate": assignment.get("TechnicalReplicate", ""),
            "Cultivation ID": assignment.get("Cultivation", ""),
            "Cultivation experiment range": assignment.get("CultivationExperiment", ""),
            "Local ID": assignment.get("Local_Cultivation_ID", ""),
            "Internal ID": assignment.get("InternalCultivationID", ""),
            "Previous IDs": _previous_ids_cell(assignment.get("PreviousCultivationIDs")),
        }
        for plan in preview.plates
        for assignment in plan.assignments
    ]
    if assignments:
        st.dataframe(pd.DataFrame(assignments), hide_index=True, width="stretch")
    for plan in preview.plates:
        for warning in plan.warnings:
            st.warning(f"{plan.plate_id}: {warning}")


def _previous_ids_cell(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return value if isinstance(value, str) else ""


def _render_prepared_bundle(
    bundle: GrowthTabularExportBundle, selected: tuple[PlateId, ...], *, legacy: bool
) -> None:
    """Offer the two CSVs only for the current selection and settings."""

    left, middle, right = st.columns(3)
    left.metric("Runs", len(selected))
    middle.metric("OD observation rows", bundle.measurements.row_count)
    right.metric("Cultivation metadata rows", bundle.metadata.row_count)
    for warning in bundle.warnings:
        st.warning(warning)
    if legacy and bundle.replicate_preview:
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


def _clear_registry_preview() -> None:
    st.session_state.pop("growth_registry_preview", None)
    st.session_state.pop("growth_registry_signature", None)
