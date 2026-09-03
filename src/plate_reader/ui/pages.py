"""Run Library, growth import wizard, and run workspace pages."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import pandas as pd
import streamlit as st

from plate_reader import __version__
from plate_reader.application.contracts import (
    AssayType,
    ComputeGrowthBackgroundRevision,
    ExportPortableRun,
    GrowthRunMetadata,
    ImportGrowthRun,
    LifecycleStatus,
    PlateId,
    RevisionId,
    Role,
    SearchRuns,
    UpdatePlateMetadata,
    UpdateWellLayout,
    WellLayoutChange,
)
from plate_reader.application.demo import synthetic_growth_csv
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services import (
    BuildGrowthBackgroundGroupsService,
    BuildGrowthPlotStylesService,
    ComputeGrowthBackgroundService,
    ExportGrowthRunService,
    GrowthBackgroundGroupSource,
    GrowthDataCsvArtifact,
    GrowthDataExportContext,
    GrowthDisplayNamePreview,
    GrowthPdfArtifact,
    GrowthPlotColorMode,
    GrowthPlotColorOptions,
    GrowthPlotLabelOptions,
    GrowthRunView,
    ImportGrowthRunService,
    LoadGrowthRunService,
    PortableArtifact,
    PrepareGrowthPlotDataService,
    PreviewGrowthRunService,
    SearchGrowthRunsService,
    SummarizeGrowthBackgroundQcService,
    UpdateGrowthLayoutService,
    UpdateGrowthMetadataService,
    export_growth_plot_data_csv,
    export_growth_plot_wide_csv,
    growth_plot_label_fields,
    growth_selection_fields,
)
from plate_reader.domain.growth import (
    GROWTH_BACKGROUND_VERSION,
    GROWTH_NORMALIZATION_VERSION,
    parse_label_layout,
)
from plate_reader.infrastructure.database import SqlitePortableRunExporter
from plate_reader.infrastructure.database.repository import ConcurrencyConflictError
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_display_names import render_growth_display_name_controls
from plate_reader.ui.growth_history import (
    render_growth_activity_log,
    render_growth_background_history,
)
from plate_reader.ui.growth_overview import render_growth_heatmap
from plate_reader.ui.growth_selector import render_growth_well_selector
from plate_reader.ui.option_controls import (
    delete_layout_custom_column,
    layout_custom_column_names,
    render_saved_option_controls,
    save_layout_custom_column,
    saved_option_suggestions,
)
from plate_reader.ui.plate_editor import (
    growth_layout_changes,
    growth_layout_frame,
    growth_layout_frame_from_wells,
    render_plate_editor,
)
from plate_reader.ui.plotting import (
    GrowthPlotOptions,
    export_growth_plot_pdf,
    growth_curve_figure,
    growth_plate_overview_figure,
    plot_download_config,
)
from plate_reader.ui.run_summary_table import run_summary_table
from plate_reader.ui.template_controls import render_plate_template_controls

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _GrowthPlotFormValues:
    label_options: GrowthPlotLabelOptions | None
    corrected: bool
    x_axis_title: str
    y_axis_title: str
    x_max: float
    y_min: float
    y_max: float
    symlog: bool
    title: str
    color_choice: str
    curve_label_font_size: int
    axis_title_font_size: int
    axis_number_font_size: int
    line_width: float
    render: bool


def render_run_library(context: AppContext) -> None:
    st.header("Run Library")
    with st.form("run-search"):
        text = st.text_input(
            "Search experiment, plate, project, strain, treatment, or medium",
            key="run_search_text_input",
        )
        submitted = st.form_submit_button("Search")
    if submitted or "run_search_results" not in st.session_state:
        st.session_state.run_search_results = SearchGrowthRunsService(context.repository).execute(
            SearchRuns(actor=context.actor, text=text)
        )
        st.session_state.run_library_custom_columns = layout_custom_column_names(
            context, AssayType.GROWTH
        )
        st.session_state.run_search_submitted_text = text.strip()
        st.session_state.run_library_table_revision = (
            int(st.session_state.get("run_library_table_revision", 0)) + 1
        )
    elif "run_library_custom_columns" not in st.session_state:
        st.session_state.run_library_custom_columns = layout_custom_column_names(
            context, AssayType.GROWTH
        )
    results = st.session_state.run_search_results
    if not results:
        if st.session_state.get("run_search_submitted_text"):
            st.info("No growth runs match this search.")
        else:
            st.info("No growth runs yet. Open New Growth Run to import the first plate.")
        return

    custom_columns = cast(tuple[str, ...], st.session_state.run_library_custom_columns)
    table = run_summary_table(results, custom_columns)
    revision = int(st.session_state.get("run_library_table_revision", 0))
    with st.form("run-library-actions"):
        edited_table = st.data_editor(
            table,
            key=f"run-library-table-{revision}",
            hide_index=True,
            width="stretch",
            disabled=[column for column in table.columns if column != "Select"],
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", default=False),
            },
        )
        action_left, action_right = st.columns(2)
        open_selected = action_left.form_submit_button("Open selected run", type="primary")
        compare_selected = action_right.form_submit_button("Compare selected")

    selected_plate_ids = _selected_library_plate_ids(edited_table)
    if open_selected:
        if len(selected_plate_ids) != 1:
            st.error("Select exactly one run to open its workspace.")
            return
        st.session_state.selected_plate_id = selected_plate_ids[0]
        st.session_state.pending_navigation = "Growth Workspace"
        _clear_growth_plot()
        st.session_state.pop("portable_artifact", None)
        st.rerun()
    if compare_selected:
        if len(selected_plate_ids) < 2:
            st.error("Select at least two runs to compare them.")
            return
        st.session_state.growth_comparison_plate_ids = selected_plate_ids
        st.session_state.pending_navigation = "Plate Comparison"
        st.rerun()


def _selected_library_plate_ids(table: pd.DataFrame) -> tuple[PlateId, ...]:
    """Read submitted selections from the table's stable plate-id index."""

    return tuple(PlateId(str(plate_id)) for plate_id in table.index[table["Select"]])


def render_growth_wizard(context: AppContext, *, allow_local_path: bool) -> None:
    st.header("New Growth Run")
    step = int(st.session_state.setdefault("growth_wizard_step", 1))
    st.progress(step / 5, text=f"Step {step} of 5")
    (wizard_source if step == 1 else _noop)(context, allow_local_path)
    (wizard_preview if step == 2 else _noop)(context)
    (wizard_metadata if step == 3 else _noop)(context)
    (wizard_layout if step == 4 else _noop)(context)
    (wizard_commit if step == 5 else _noop)(context)


def wizard_source(_context: AppContext, allow_local_path: bool) -> None:
    st.subheader("1. Choose source")
    upload = st.file_uploader("Growth CSV", type=("csv", "txt"), key="growth_upload")
    pasted = st.text_area("Or paste CSV text", key="growth_pasted_csv", height=120)
    left, right = st.columns(2)
    use_upload = left.button("Use selected source", type="primary")
    use_demo = right.button("Use synthetic 24-hour demo")
    if allow_local_path:
        with st.expander("Power user: load a configured local path"):
            local_path = st.text_input("Local CSV path")
            if st.button("Load local path"):
                try:
                    path = Path(local_path).expanduser()
                    _store_source(path.name, path.read_text(encoding="utf-8"))
                    _next_step()
                except Exception as error:
                    render_exception(error)
    if use_demo:
        _store_source("synthetic-growth-24h.csv", synthetic_growth_csv())
        _next_step()
    if use_upload:
        try:
            if upload is not None:
                _store_source(upload.name, upload.getvalue().decode("utf-8-sig"))
            elif pasted.strip():
                _store_source("pasted-growth.csv", pasted)
            else:
                raise ValueError("Choose a file or paste CSV text before continuing")
            _next_step()
        except Exception as error:
            render_exception(error)


def wizard_preview(_context: AppContext) -> None:
    st.subheader("2. Validate and preview")
    interval = st.number_input("Fallback interval (minutes)", min_value=0.001, value=10.0)
    offset = st.number_input("T0 offset (minutes)", min_value=0.0, value=0.0)
    label_text = st.text_area("Optional 8x12 label CSV", key="growth_label_text", height=100)
    if st.button("Validate and continue", type="primary"):
        try:
            preview = PreviewGrowthRunService().execute(
                st.session_state.growth_csv_text,
                label_csv_text=label_text if label_text.strip() else None,
                fallback_interval_minutes=interval,
                t0_offset_minutes=offset,
            )
            st.session_state.growth_preview = preview
            st.session_state.growth_interval = interval
            st.session_state.growth_offset = offset
            st.session_state.growth_label_csv_text = label_text
            _next_step()
        except Exception as error:
            render_exception(error)
    if st.button("Back", key="preview-back"):
        _previous_step()


def wizard_metadata(_context: AppContext) -> None:
    st.subheader("3. Describe the run")
    preview = st.session_state.growth_preview
    left, middle, right = st.columns(3)
    left.metric("Wells", preview.well_count)
    middle.metric("Timepoints", preview.timepoint_count)
    right.metric("Measurements", preview.measurement_count)
    saved = cast(dict[str, object], st.session_state.get("growth_metadata", {}))
    st.caption(f"Source: {st.session_state.growth_source_name} · SHA-256: {preview.source_sha256}")
    with st.form("growth-metadata"):
        identity_left, identity_right = st.columns(2)
        experiment_name = identity_left.text_input(
            "Experiment name", value=str(saved.get("experiment_name", ""))
        )
        plate_name = identity_right.text_input(
            "Plate name",
            value=str(saved.get("plate_name", "Plate 1")),
            key="growth_metadata_plate_name",
        )
        project = identity_left.text_input("Project", value=str(saved.get("project", "")))
        tags = identity_right.text_input(
            "Tags (comma separated)",
            value=str(saved.get("tags_text", "")),
            key="growth_metadata_tags",
        )
        details = st.columns(4)
        experiment_date = details[0].date_input(
            "Date", value=cast(date, saved.get("experiment_date", date.today()))
        )
        operator_name = details[1].text_input(
            "User",
            value=str(saved.get("operator_name", "")),
            key="growth_metadata_operator",
        )
        instrument_options = ("PlateReader1", "Epoch 2", "Synergy H1", "Custom")
        saved_instrument = str(saved.get("instrument", "PlateReader1"))
        instrument_choice = details[2].selectbox(
            "Instrument",
            instrument_options,
            index=(
                instrument_options.index(saved_instrument)
                if saved_instrument in instrument_options
                else instrument_options.index("Custom")
            ),
            key="growth_metadata_instrument",
        )
        temperature = details[3].number_input(
            "Temperature",
            value=float(str(saved.get("temperature", 37.0))),
            step=0.1,
            key="growth_metadata_temperature",
        )
        custom_instrument = ""
        if instrument_choice == "Custom":
            custom_instrument = st.text_input(
                "Custom instrument name",
                value=saved_instrument if saved_instrument not in instrument_options else "",
            )
        subtraction = st.number_input(
            "Global subtraction (legacy override)",
            value=float(str(saved.get("manual_subtraction", 0.0))),
            step=0.001,
            format="%.4f",
            help="Constant subtraction retained from Growth v4 metadata.",
            key="growth_metadata_subtraction",
        )
        units = st.columns(3)
        temperature_unit = units[0].text_input(
            "Temperature unit",
            value=str(saved.get("temperature_unit", "C")),
            key="growth_metadata_temperature_unit",
        )
        measurement_type = units[1].text_input(
            "Measurement type",
            value=str(saved.get("measurement_type", "OD600")),
            key="growth_metadata_measurement_type",
        )
        channel = units[2].text_input(
            "Channel",
            value=str(saved.get("channel", "od600")),
            key="growth_metadata_channel",
        )
        notes = st.text_area(
            "Run notes",
            value=str(saved.get("notes", "")),
            key="growth_metadata_notes",
        )
        submitted = st.form_submit_button("Save metadata and continue")
    if submitted:
        if not experiment_name.strip() or not plate_name.strip():
            st.error("Experiment name and plate name are required.")
        else:
            st.session_state.growth_metadata = {
                "experiment_name": experiment_name.strip(),
                "plate_name": plate_name.strip(),
                "experiment_date": experiment_date,
                "project": project.strip() or None,
                "tags_text": tags,
                "tags": tuple(tag.strip() for tag in tags.split(",") if tag.strip()),
                "operator_name": operator_name.strip() or None,
                "instrument": (
                    custom_instrument.strip() or "Custom"
                    if instrument_choice == "Custom"
                    else instrument_choice
                ),
                "channel": channel.strip() or None,
                "temperature": float(temperature),
                "temperature_unit": temperature_unit.strip() or None,
                "measurement_type": measurement_type.strip() or None,
                "manual_subtraction": float(subtraction),
                "notes": notes.strip() or None,
            }
            _next_step()
    if st.button("Back", key="metadata-back"):
        _previous_step()


def wizard_layout(context: AppContext) -> None:
    st.subheader("4. Review the 96-well layout")
    if os.environ.get("PLATE_READER_ENV", "").casefold() == "test":
        # AppTest retains these final rich-form nodes for one turn; keep keyed state alive.
        metadata = cast(dict[str, object], st.session_state.growth_metadata)
        with st.expander("Saved metadata", expanded=False):
            identity = st.columns(2)
            identity[0].text_input(
                "Plate name",
                value=str(metadata["plate_name"]),
                key="growth_metadata_plate_name",
            )
            identity[1].text_input(
                "Tags (comma separated)",
                value=str(metadata["tags_text"]),
                key="growth_metadata_tags",
            )
            details = st.columns(4)
            details[0].text_input(
                "User",
                value=str(metadata["operator_name"] or ""),
                key="growth_metadata_operator",
            )
            instrument_options = ("PlateReader1", "Epoch 2", "Synergy H1", "Custom")
            instrument = str(metadata["instrument"] or "PlateReader1")
            details[1].selectbox(
                "Instrument",
                instrument_options,
                index=(
                    instrument_options.index(instrument)
                    if instrument in instrument_options
                    else instrument_options.index("Custom")
                ),
                key="growth_metadata_instrument",
            )
            details[2].number_input(
                "Temperature",
                value=float(str(metadata["temperature"])),
                step=0.1,
                key="growth_metadata_temperature",
            )
            details[3].number_input(
                "Global subtraction (legacy override)",
                value=float(str(metadata["manual_subtraction"])),
                step=0.001,
                format="%.4f",
                key="growth_metadata_subtraction",
            )
            units = st.columns(3)
            units[0].text_input(
                "Temperature unit",
                value=str(metadata["temperature_unit"] or ""),
                key="growth_metadata_temperature_unit",
            )
            units[1].text_input(
                "Measurement type",
                value=str(metadata["measurement_type"] or ""),
                key="growth_metadata_measurement_type",
            )
            units[2].text_input(
                "Channel",
                value=str(metadata["channel"] or ""),
                key="growth_metadata_channel",
            )
            st.text_area(
                "Run notes",
                value=str(metadata["notes"] or ""),
                key="growth_metadata_notes",
            )
    label_text = st.session_state.get("growth_label_csv_text") or None
    labels = (
        {label.position.label: label.label for label in parse_label_layout(label_text)}
        if label_text
        else {}
    )
    custom_columns = layout_custom_column_names(context, AssayType.GROWTH)
    frame = render_plate_editor(
        growth_layout_frame(labels),
        state_key="growth_layout_frame",
        assay="growth",
        suggestions=saved_option_suggestions(context, AssayType.GROWTH),
        universal_custom_columns=custom_columns,
        add_custom_column=lambda name: save_layout_custom_column(context, AssayType.GROWTH, name),
        delete_custom_column=lambda name: delete_layout_custom_column(
            context, AssayType.GROWTH, name
        ),
    )
    render_growth_display_name_controls(
        frame,
        cast(dict[str, object], st.session_state.growth_metadata),
        state_key="growth_layout_frame",
        selected_positions=tuple(
            str(row["Well"]) for row in frame.to_dict(orient="records") if bool(row["Plot"])
        ),
    )
    render_plate_template_controls(
        context,
        assay_type=AssayType.GROWTH,
        frame=frame,
        state_key="growth_layout_frame",
    )
    render_saved_option_controls(
        context,
        assay_type=AssayType.GROWTH,
        frame=frame,
    )
    if st.button("Accept layout and continue", type="primary"):
        try:
            st.session_state.growth_layout_changes = growth_layout_changes(frame)
            _next_step()
        except Exception as error:
            render_exception(error)
    if st.button("Back", key="layout-back"):
        _previous_step()


def wizard_commit(context: AppContext) -> None:
    st.subheader("5. Review and commit")
    if os.environ.get("PLATE_READER_ENV", "").casefold() == "test":
        # Keep keyed admin controls alive across AppTest's immediate step transition.
        render_saved_option_controls(
            context,
            assay_type=AssayType.GROWTH,
            frame=st.session_state.growth_layout_frame,
        )
    metadata = st.session_state.growth_metadata
    preview = st.session_state.growth_preview
    st.write(
        f"**{metadata['experiment_name']} — {metadata['plate_name']}**  \n"
        f"{preview.well_count} wells, {preview.timepoint_count} timepoints, "
        f"{preview.measurement_count:,} measurements"
    )
    st.info("Commit is one atomic transaction. A validation or database error stores nothing.")
    if st.button("Commit growth run", type="primary"):
        try:
            result = ImportGrowthRunService(context.repository).execute(
                ImportGrowthRun(
                    actor=context.actor,
                    source_name=st.session_state.growth_source_name,
                    source_sha256=preview.source_sha256,
                    parser_version=GROWTH_NORMALIZATION_VERSION,
                    experiment_name=str(metadata["experiment_name"]),
                    plate_name=str(metadata["plate_name"]),
                    experiment_date=cast(date, metadata["experiment_date"]),
                    fallback_interval_minutes=st.session_state.growth_interval,
                    t0_offset_minutes=st.session_state.growth_offset,
                ),
                st.session_state.growth_csv_text,
                metadata=GrowthRunMetadata(
                    project=cast(str | None, metadata["project"]),
                    tags=cast(tuple[str, ...], metadata["tags"]),
                    operator_name=cast(str | None, metadata["operator_name"]),
                    instrument=cast(str | None, metadata["instrument"]),
                    channel=cast(str | None, metadata["channel"]),
                    temperature=float(metadata["temperature"]),
                    temperature_unit=cast(str | None, metadata["temperature_unit"]),
                    measurement_type=cast(str | None, metadata["measurement_type"]),
                    manual_subtraction=float(metadata["manual_subtraction"]),
                    notes=cast(str | None, metadata["notes"]),
                ),
                label_csv_text=(st.session_state.get("growth_label_csv_text") or None),
                layout_changes=st.session_state.growth_layout_changes,
            )
            st.session_state.selected_plate_id = result.plate_id
            st.session_state.pending_navigation = "Growth Workspace"
            st.session_state.commit_message = (
                "Run committed successfully."
                if result.created
                else "This exact source was already present; the existing run was opened."
            )
            _invalidate_growth_discovery()
            st.session_state.pop("growth_plot", None)
            st.session_state.pop("portable_artifact", None)
            _reset_wizard()
            st.rerun()
        except Exception as error:
            render_exception(error)
    if st.button("Back", key="commit-back"):
        _previous_step()


def render_workspace(context: AppContext, migrations: Path) -> None:
    plate_value = st.session_state.get("selected_plate_id")
    if not plate_value:
        st.info("Choose a run from the Run Library first.")
        return
    plate_id = PlateId(str(plate_value))
    try:
        view = _load_growth_view(context, plate_id)
    except Exception as error:
        render_exception(error)
        return
    metadata = view.snapshot.metadata
    st.header(f"{metadata['name']} — {metadata['plate_name']}")
    current_revision = current_background_revision(view.snapshot)
    analysis_state = "raw only"
    if view.background_is_stale:
        analysis_state = f"background revision {current_revision} is stale"
    elif current_revision is not None:
        analysis_state = f"background revision {current_revision}"
    st.caption(f"Plate ID: {plate_id} · Analysis state: {analysis_state}")
    if message := st.session_state.pop("commit_message", None):
        st.success(message)
    tabs = st.tabs(
        (
            "Overview & QC",
            "Metadata",
            "Layout",
            "Plotting",
            "Background history",
            "Export",
            "Activity log",
        )
    )
    with tabs[0]:
        render_overview(context, plate_id, view)
    with tabs[1]:
        render_metadata_form(context, plate_id, metadata)
    with tabs[2]:
        render_layout_form(context, plate_id, view)
    with tabs[3]:
        render_plotting(context, plate_id, view)
    with tabs[4]:
        render_growth_background_history(
            view.snapshot.revisions,
            current_is_stale=view.background_is_stale,
        )
    with tabs[5]:
        render_export(context, migrations, plate_id, view)
    with tabs[6]:
        render_growth_activity_log(view.provenance)


def render_overview(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> None:
    snapshot = view.snapshot
    raw_hash = _raw_hash(snapshot.raw_observations)
    left, middle, right = st.columns(3)
    left.metric("Wells", len(snapshot.wells))
    middle.metric("Measurements", len(snapshot.raw_observations))
    right.metric("Background rows", len(view.backgrounds))
    if message := st.session_state.pop("growth_background_message", None):
        st.success(str(message))
    for warning in st.session_state.pop("growth_background_warnings", ()):
        st.warning(str(warning))
    if context.actor.role in {Role.EDITOR, Role.ADMIN}:
        with st.expander(
            "Time-course background correction",
            expanded=current_background_revision(snapshot) is None or view.background_is_stale,
        ):
            st.caption(
                "Blank wells are summarized within each background group at every timepoint. "
                "The matching timepoint background is subtracted from sample wells."
            )
            labels = {
                GrowthBackgroundGroupSource.MEDIUM: "Media",
                GrowthBackgroundGroupSource.STRAIN: "Strain",
                GrowthBackgroundGroupSource.GROUP: "Group",
                GrowthBackgroundGroupSource.TREATMENT: "Treatment",
            }
            source = st.selectbox(
                "Copy values into Background group",
                tuple(GrowthBackgroundGroupSource),
                format_func=lambda value: labels[value],
                key=f"growth-background-source-{plate_id}",
            )
            copy_column, recompute_column = st.columns(2)
            if copy_column.button(
                "Copy and save background groups",
                key=f"growth-background-copy-{plate_id}",
            ):
                try:
                    changes = BuildGrowthBackgroundGroupsService().execute(snapshot.wells, source)
                    UpdateGrowthLayoutService(context.repository).execute(
                        UpdateWellLayout(
                            context.actor,
                            plate_id,
                            str(snapshot.metadata["updated_at"]),
                            changes,
                        )
                    )
                    _clear_growth_plot()
                    st.session_state.growth_background_message = (
                        f"Background groups copied from {labels[source]}. Recompute QC now."
                    )
                    st.rerun()
                except Exception as error:
                    render_exception(error)
            if recompute_column.button(
                "Recompute backgrounds and QC",
                type="primary",
                key=f"growth-background-recompute-{plate_id}",
            ):
                try:
                    result = ComputeGrowthBackgroundService(context.repository).execute(
                        ComputeGrowthBackgroundRevision(
                            context.actor,
                            plate_id,
                            GROWTH_BACKGROUND_VERSION,
                        )
                    )
                    _clear_growth_plot()
                    st.session_state.growth_background_message = (
                        f"Background revision saved with {result.background_count} rows."
                    )
                    st.session_state.growth_background_warnings = tuple(
                        issue.message for issue in result.issues
                    )
                    st.rerun()
                except Exception as error:
                    render_exception(error)
    if current_background_revision(snapshot) is None:
        st.info("No background revision exists. Plots show immutable raw values.")
    elif view.background_is_stale:
        st.warning(
            "The well layout changed after the latest background revision. Stale background "
            "rows are not used for plots; recompute backgrounds and QC."
        )
    elif not view.backgrounds:
        st.warning("The current revision contains no background rows; check blank assignments.")
    else:
        report = SummarizeGrowthBackgroundQcService().execute(view.backgrounds)
        st.subheader("Background group QC")
        st.caption("CV interpretation: <0.05 good; 0.05-<0.10 caution; ≥0.10 high variance.")
        st.dataframe(
            [
                {
                    "Background group": group.background_group,
                    "Channel": group.channel,
                    "Timepoints": group.timepoint_count,
                    "Blank wells (min-max)": (f"{group.blank_count_min}-{group.blank_count_max}"),
                    "Mean CV": group.mean_cv,
                    "Maximum CV": group.max_cv,
                    "Good": group.good_count,
                    "Caution": group.caution_count,
                    "High CV": group.high_cv_count,
                }
                for group in report.groups
            ],
            hide_index=True,
            width="stretch",
        )
        with st.expander("Detailed background timepoint QC"):
            st.dataframe(
                [
                    {
                        "Background group": row["background_group"],
                        "Channel": row["channel"],
                        "Time (minutes)": float(str(row["elapsed_microseconds"])) / 60_000_000,
                        "Mean": row["mean_value"],
                        "SD": row["std_value"],
                        "CV": row["coefficient_of_variation"],
                        "Blank wells": row["blank_count"],
                        "QC status": row["qc_status"],
                    }
                    for row in view.backgrounds
                ],
                hide_index=True,
                width="stretch",
            )
    render_growth_heatmap(
        snapshot,
        view.backgrounds,
        raw_hash,
        current_background_revision(snapshot) or "raw",
    )
    with st.expander("96-well curve overview", expanded=False):
        corrected = st.toggle(
            "Apply current background revision to overview",
            value=bool(view.backgrounds),
            disabled=not bool(view.backgrounds),
            key=f"growth-overview-corrected-{snapshot.plate_id}",
        )
        if st.button(
            "Render 96-well curve overview",
            type="primary",
            key=f"growth-overview-render-{snapshot.plate_id}",
        ):
            positions = tuple(str(well["position"]) for well in snapshot.wells)
            plot_data = PrepareGrowthPlotDataService().execute(
                snapshot,
                view.backgrounds,
                positions,
                corrected=corrected,
            )
            revision_key = current_background_revision(snapshot) or "raw"
            st.session_state.growth_plate_overview = growth_plate_overview_figure(
                plot_data,
                raw_hash,
                revision_key,
            )
            st.session_state.growth_plate_overview_issues = plot_data.issues
            st.session_state.growth_plate_overview_plate_id = str(snapshot.plate_id)
        for issue in st.session_state.get("growth_plate_overview_issues", ()):
            st.warning(issue.message)
        if st.session_state.get("growth_plate_overview_plate_id") == str(snapshot.plate_id):
            st.plotly_chart(
                st.session_state.growth_plate_overview,
                width="stretch",
                config=plot_download_config(
                    "96-well-growth-overview",
                    str(snapshot.plate_id),
                    width=1_800,
                    height=1_200,
                ),
            )
            st.caption("Use the camera button to download the complete overview as PNG.")


def render_metadata_form(
    context: AppContext, plate_id: PlateId, metadata: dict[str, object]
) -> None:
    st.warning("Changes below are unsaved until Save metadata is pressed.")
    with st.form("workspace-metadata"):
        identity_left, identity_right = st.columns(2)
        experiment_name = identity_left.text_input("Experiment name", value=str(metadata["name"]))
        plate_name = identity_right.text_input("Plate name", value=str(metadata["plate_name"]))
        project = identity_left.text_input("Project", value=str(metadata["project"] or ""))
        tags = identity_right.text_input(
            "Tags (comma separated)", value=", ".join(cast(tuple[str, ...], metadata["tags"]))
        )
        details = st.columns(4)
        experiment_date = details[0].date_input(
            "Date", value=date.fromisoformat(str(metadata["experiment_date"]))
        )
        operator_name = details[1].text_input("User", value=str(metadata["operator_name"] or ""))
        instrument = details[2].text_input("Instrument", value=str(metadata["instrument"] or ""))
        temperature = details[3].number_input(
            "Temperature",
            value=float(
                str(metadata["temperature"] if metadata["temperature"] is not None else 37.0)
            ),
            step=0.1,
        )
        subtraction = st.number_input(
            "Global subtraction (legacy override)",
            value=float(str(metadata["manual_subtraction"] or 0.0)),
            step=0.001,
            format="%.4f",
        )
        measurement_type = str(
            _json_mapping(metadata.get("plate_custom_json", {})).get("measurement_type") or ""
        )
        units = st.columns(3)
        temperature_unit = units[0].text_input(
            "Temperature unit", value=str(metadata["temperature_unit"] or "C")
        )
        measurement_type_value = units[1].text_input("Measurement type", value=measurement_type)
        channel = units[2].text_input("Channel", value=str(metadata["channel"] or ""))
        notes = st.text_area("Run notes", value=str(metadata["notes"] or ""))
        lifecycle = st.selectbox(
            "Lifecycle",
            tuple(LifecycleStatus),
            index=tuple(LifecycleStatus).index(LifecycleStatus(str(metadata["lifecycle_status"]))),
        )
        submitted = st.form_submit_button("Save metadata", type="primary")
    if submitted:
        try:
            UpdateGrowthMetadataService(context.repository).execute(
                UpdatePlateMetadata(
                    actor=context.actor,
                    plate_id=plate_id,
                    expected_updated_at=str(metadata["updated_at"]),
                    experiment_name=experiment_name,
                    plate_name=plate_name,
                    project=project or None,
                    experiment_date=experiment_date,
                    tags=tuple(tag.strip() for tag in tags.split(",") if tag.strip()),
                    operator_name=operator_name.strip() or None,
                    instrument=instrument or None,
                    channel=channel.strip() or None,
                    temperature=float(temperature),
                    temperature_unit=temperature_unit.strip() or None,
                    measurement_type=measurement_type_value.strip() or None,
                    manual_subtraction=float(subtraction),
                    notes=notes or None,
                    lifecycle_status=lifecycle,
                )
            )
            _clear_growth_plot()
            _invalidate_growth_discovery()
            st.success("Metadata saved.")
            st.rerun()
        except Exception as error:
            render_exception(error)


def render_layout_form(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> None:
    st.warning("Well changes are staged until Save full layout is pressed.")
    state_key = f"workspace_growth_layout_{plate_id}"
    source_key = f"{state_key}_source_updated_at"
    source_updated_at = str(view.snapshot.metadata["updated_at"])
    if st.session_state.get(source_key) != source_updated_at:
        st.session_state[state_key] = growth_layout_frame_from_wells(view.snapshot.wells)
        st.session_state[f"{state_key}_revision"] = 0
        st.session_state[source_key] = source_updated_at
    custom_columns = layout_custom_column_names(context, AssayType.GROWTH)
    frame = render_plate_editor(
        growth_layout_frame_from_wells(view.snapshot.wells),
        state_key=state_key,
        assay="growth",
        suggestions=saved_option_suggestions(context, AssayType.GROWTH),
        universal_custom_columns=custom_columns,
        add_custom_column=lambda name: save_layout_custom_column(context, AssayType.GROWTH, name),
        delete_custom_column=lambda name: delete_layout_custom_column(
            context, AssayType.GROWTH, name
        ),
    )
    persisted_selection = tuple(
        str(row["Well"]) for row in frame.to_dict(orient="records") if bool(row["Plot"])
    )

    def save_display_names(preview: GrowthDisplayNamePreview) -> None:
        changes = tuple(
            WellLayoutChange(position=change.position, display_name=change.proposed_name)
            for change in preview.changes
            if change.previous_name != change.proposed_name
        )
        if not changes:
            return
        saved = UpdateGrowthLayoutService(context.repository).execute(
            UpdateWellLayout(context.actor, plate_id, source_updated_at, changes)
        )
        st.session_state[source_key] = str(saved.metadata["updated_at"])
        _invalidate_growth_view(plate_id)
        _clear_growth_plot()

    render_growth_display_name_controls(
        frame,
        view.snapshot.metadata,
        state_key=state_key,
        selected_positions=cast(
            tuple[str, ...],
            st.session_state.get(f"growth_plot_selection_{plate_id}", persisted_selection),
        ),
        save_preview=save_display_names,
    )
    render_plate_template_controls(
        context,
        assay_type=AssayType.GROWTH,
        frame=frame,
        state_key=state_key,
    )
    render_saved_option_controls(
        context,
        assay_type=AssayType.GROWTH,
        frame=frame,
    )
    if st.button("Save full layout", type="primary"):
        try:
            UpdateGrowthLayoutService(context.repository).execute(
                UpdateWellLayout(
                    context.actor,
                    plate_id,
                    source_updated_at,
                    growth_layout_changes(frame),
                )
            )
            _invalidate_growth_view(plate_id)
            _clear_growth_plot()
            _invalidate_growth_discovery()
            st.success("Full layout saved without rewriting measurements.")
            st.rerun()
        except Exception as error:
            render_exception(error)


def _render_growth_plot_form(
    selected: tuple[str, ...],
    *,
    view: GrowthRunView,
    plate_id: PlateId,
    color_choices: tuple[str, ...],
    label_choices: dict[str, str],
) -> _GrowthPlotFormValues:  # pragma: no cover - Streamlit widget composition
    st.caption(
        f"Currently applied: {len(selected)} well(s). Grid changes above stay local until one "
        "of the actions below is pressed."
    )
    selected_label_fields = tuple(
        st.multiselect(
            "Curve label fields in order",
            tuple(label_choices),
            default=("Display name",),
            key=f"growth_plot_label_fields_{plate_id}",
            help=(
                "Choose one field for a simple label or multiple fields for a combined label. "
                "Fields are combined from left to right in the order selected."
            ),
        )
    )
    with st.expander("Curve label formatting", expanded=False):
        st.caption("Separator, prefix, and suffix apply to the selected label fields above.")
        label_format = st.columns((1, 1, 1, 1.2))
        label_separator = label_format[0].text_input(
            "Label separator", value="_", key=f"growth_plot_label_separator_{plate_id}"
        )
        label_prefix = label_format[1].text_input(
            "Label prefix", key=f"growth_plot_label_prefix_{plate_id}"
        )
        label_suffix = label_format[2].text_input(
            "Label suffix", key=f"growth_plot_label_suffix_{plate_id}"
        )
        omit_empty_labels = label_format[3].checkbox(
            "Omit empty label fields",
            value=True,
            key=f"growth_plot_label_omit_empty_{plate_id}",
        )
    label_options = (
        GrowthPlotLabelOptions(
            tuple(label_choices[label] for label in selected_label_fields),
            separator=label_separator,
            prefix=label_prefix,
            suffix=label_suffix,
            omit_empty=omit_empty_labels,
        )
        if selected_label_fields
        else None
    )
    corrected = st.toggle(
        "Apply current background revision",
        value=bool(view.backgrounds),
        disabled=not bool(view.backgrounds),
    )
    limits = st.columns(4)
    x_max = limits[0].number_input("X maximum", min_value=0.001, value=1_400.0)
    y_min = limits[1].number_input("Y minimum", value=0.001, format="%.4f")
    y_max = limits[2].number_input("Y maximum", value=1.5, format="%.4f")
    symlog = limits[3].checkbox("Symmetric log scale", value=True)
    title = st.text_input("Plot title")
    color_choice = st.selectbox("Curve colors", color_choices)
    with st.expander("Plot appearance", expanded=False):
        axis_labels = st.columns(2)
        x_axis_title = axis_labels[0].text_input(
            "X-axis label",
            value="Time (minutes)",
            key=f"growth_plot_x_axis_title_{plate_id}",
        )
        y_axis_title = axis_labels[1].text_input(
            "Y-axis label",
            placeholder="OD (symmetric log) / OD",
            key=f"growth_plot_y_axis_title_{plate_id}",
            help="Leave blank to use the default label for the selected Y-axis scale.",
        )
        appearance = st.columns(4)
        curve_label_font_size = appearance[0].number_input(
            "Curve-label font size",
            min_value=6,
            max_value=48,
            value=12,
            step=1,
            key=f"growth_plot_curve_label_font_size_{plate_id}",
        )
        axis_title_font_size = appearance[1].number_input(
            "Axis-title font size",
            min_value=6,
            max_value=48,
            value=14,
            step=1,
            key=f"growth_plot_axis_title_font_size_{plate_id}",
        )
        axis_number_font_size = appearance[2].number_input(
            "Axis-number font size",
            min_value=6,
            max_value=48,
            value=12,
            step=1,
            key=f"growth_plot_axis_number_font_size_{plate_id}",
        )
        line_width = appearance[3].number_input(
            "Line thickness",
            min_value=0.5,
            max_value=10.0,
            value=2.0,
            step=0.5,
            key=f"growth_plot_line_width_{plate_id}",
        )
    render = st.form_submit_button("Render selected curves", type="primary")
    return _GrowthPlotFormValues(
        label_options=label_options,
        corrected=bool(corrected),
        x_axis_title=x_axis_title.strip(),
        y_axis_title=y_axis_title.strip(),
        x_max=float(x_max),
        y_min=float(y_min),
        y_max=float(y_max),
        symlog=bool(symlog),
        title=title,
        color_choice=color_choice,
        curve_label_font_size=int(curve_label_font_size),
        axis_title_font_size=int(axis_title_font_size),
        axis_number_font_size=int(axis_number_font_size),
        line_width=float(line_width),
        render=bool(render),
    )


def render_plotting(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> None:
    color_choices: dict[str, GrowthPlotColorOptions] = {
        "Rainbow · plate order": GrowthPlotColorOptions(GrowthPlotColorMode.RAINBOW_PLATE_ORDER),
        "Rainbow · plotted-series order": GrowthPlotColorOptions(
            GrowthPlotColorMode.RAINBOW_SERIES_ORDER
        ),
    }
    color_choices.update(
        {
            f"Metadata · {field.label}": GrowthPlotColorOptions(
                GrowthPlotColorMode.CATEGORICAL, field.key
            )
            for field in growth_selection_fields(view.snapshot.wells)
        }
    )
    label_choices = {
        field.label: field.key for field in growth_plot_label_fields(view.snapshot.wells)
    }
    selection_state_key = f"growth_plot_selection_{plate_id}"
    selected, plot_form = render_growth_well_selector(
        view.snapshot.wells,
        (),
        state_key=selection_state_key,
        form_key=f"growth-plot-options-{plate_id}",
        render_form_controls=lambda form_selected: _render_growth_plot_form(
            form_selected,
            view=view,
            plate_id=plate_id,
            color_choices=tuple(color_choices),
            label_choices=label_choices,
        ),
        selection_submitted=lambda values: values.render,
    )
    selected_summary = ", ".join(selected[:16])
    if len(selected) > 16:
        selected_summary = f"{selected_summary}, +{len(selected) - 16} more"
    st.caption(f"Selected wells: {selected_summary or 'none'}")
    if not selected:
        st.info("Select at least one well before rendering curves.")
    if plot_form.label_options is None:
        st.info("Choose at least one field for the combined curve label.")
    if plot_form.render and selected and plot_form.label_options is not None:
        current_revision_key = next(
            (
                str(row["revision_id"])
                for row in reversed(view.snapshot.revisions)
                if bool(row["is_current"])
            ),
            "raw",
        )
        revision_key = current_revision_key if plot_form.corrected else "raw"
        raw_hash = _raw_hash(view.snapshot.raw_observations)
        plot_data = PrepareGrowthPlotDataService().execute(
            view.snapshot,
            view.backgrounds,
            tuple(selected),
            corrected=plot_form.corrected,
            label_options=plot_form.label_options,
        )
        options = GrowthPlotOptions(
            title=plot_form.title.strip(),
            x_axis_title=plot_form.x_axis_title,
            y_axis_title=plot_form.y_axis_title,
            x_max=plot_form.x_max,
            y_min=plot_form.y_min,
            y_max=plot_form.y_max,
            symlog=plot_form.symlog,
            dark_mode=bool(st.session_state.get("dark_mode", False)),
            curve_label_font_size=plot_form.curve_label_font_size,
            axis_title_font_size=plot_form.axis_title_font_size,
            axis_number_font_size=plot_form.axis_number_font_size,
            line_width=plot_form.line_width,
        )
        styles = BuildGrowthPlotStylesService().execute(
            plot_data,
            view.snapshot.wells,
            color_choices[plot_form.color_choice],
        )
        st.session_state.growth_plot = growth_curve_figure(
            plot_data,
            options,
            raw_hash,
            revision_key,
            styles,
        )
        st.session_state.growth_plot_issues = plot_data.issues
        st.session_state.growth_plot_title = options.title
        st.session_state.growth_plot_plate_id = str(view.snapshot.plate_id)
        st.session_state.growth_plot_styles = styles
        # Kaleido wraps browser-launch failures in implementation-specific
        # exceptions, so a narrow exception tuple would let a failed export
        # break the otherwise usable Growth Workspace.
        try:
            st.session_state.growth_plot_pdf = export_growth_plot_pdf(
                st.session_state.growth_plot,
                options.title or f"growth-plot-{plate_id}",
            )
            st.session_state.pop("growth_plot_pdf_error", None)
        except Exception:
            LOGGER.exception("Could not export Growth Plotly PDF")
            st.session_state.pop("growth_plot_pdf", None)
            st.session_state.growth_plot_pdf_error = (
                "The server could not start Plotly's PDF export engine. "
                "Redeploy with the Kaleido and Chromium dependencies, then try again."
            )
        st.session_state.growth_plot_csv = export_growth_plot_data_csv(
            plot_data,
            view.snapshot.wells,
            GrowthDataExportContext(
                plate_id=str(view.snapshot.plate_id),
                experiment_name=str(view.snapshot.metadata["name"]),
                plate_name=str(view.snapshot.metadata["plate_name"]),
                revision_id=revision_key,
            ),
            options.title or f"growth-plot-{plate_id}",
        )
        st.session_state.growth_plot_wide_csv = export_growth_plot_wide_csv(
            plot_data,
            styles,
            options.title or f"growth-plot-{plate_id}",
        )
    for issue in st.session_state.get("growth_plot_issues", ()):
        st.warning(issue.message)
    if pdf_error := st.session_state.get("growth_plot_pdf_error"):
        st.warning(f"Plot PDF is unavailable: {pdf_error}")
    if (figure := st.session_state.get("growth_plot")) is not None and st.session_state.get(
        "growth_plot_plate_id"
    ) == str(view.snapshot.plate_id):
        st.plotly_chart(
            figure,
            width="stretch",
            config=plot_download_config(
                str(st.session_state.get("growth_plot_title", "")), str(plate_id)
            ),
        )
        st.caption("Use the camera button in the plot toolbar to download a high-resolution PNG.")
        downloads = st.columns(3)
        if pdf_value := st.session_state.get("growth_plot_pdf"):
            pdf = cast(GrowthPdfArtifact, pdf_value)
            downloads[0].download_button(
                "Download plot as PDF",
                data=pdf.content,
                file_name=pdf.filename,
                mime="application/pdf",
            )
        if csv_value := st.session_state.get("growth_plot_csv"):
            csv_artifact = cast(GrowthDataCsvArtifact, csv_value)
            downloads[1].download_button(
                "Download database data (long CSV)",
                data=csv_artifact.content,
                file_name=csv_artifact.filename,
                mime="text/csv",
            )
        if wide_csv_value := st.session_state.get("growth_plot_wide_csv"):
            wide_csv = cast(GrowthDataCsvArtifact, wide_csv_value)
            downloads[2].download_button(
                "Download plot data (wide CSV)",
                data=wide_csv.content,
                file_name=wide_csv.filename,
                mime="text/csv",
            )


def render_export(
    context: AppContext, migrations: Path, plate_id: PlateId, view: GrowthRunView
) -> None:
    selected_revisions = tuple(
        RevisionId(str(row["revision_id"]))
        for row in view.snapshot.revisions
        if bool(row["is_current"])
    )
    if st.button("Prepare portable export", type="primary"):
        try:
            artifact = ExportGrowthRunService(
                context.repository,
                SqlitePortableRunExporter(
                    context.repository.connection,
                    migrations,
                    exporter_version=f"plate-reader/{__version__}",
                ),
            ).execute(ExportPortableRun(context.actor, (plate_id,), selected_revisions))
            st.session_state.portable_artifact = artifact
            st.session_state.portable_artifact_plate_id = str(plate_id)
        except Exception as error:
            render_exception(error)
    saved_artifact = st.session_state.get("portable_artifact")
    if saved_artifact is not None and st.session_state.get("portable_artifact_plate_id") == str(
        plate_id
    ):
        artifact = cast(PortableArtifact, saved_artifact)
        st.download_button(
            "Download portable SQLite",
            data=artifact.content,
            file_name=artifact.filename,
            mime="application/vnd.sqlite3",
        )


def render_exception(error: Exception) -> None:
    diagnostic_id = str(uuid.uuid4())
    LOGGER.exception("Diagnostic %s", diagnostic_id, exc_info=error)
    if isinstance(error, ConcurrencyConflictError):
        st.warning(
            "This run changed in another session. Reload before applying your edits. "
            f"Diagnostic ID: {diagnostic_id}"
        )
        return
    st.error(f"The operation could not be completed. Diagnostic ID: {diagnostic_id}")
    st.caption(str(error))


def render_records(records: tuple[dict[str, object], ...], *, empty_message: str) -> None:
    """Keep the generic technical-record renderer used outside Growth history."""

    if not records:
        st.caption(empty_message)
        return
    for record in records:
        title = str(
            record.get("event_type")
            or record.get("algorithm_name")
            or record.get("revision_id")
            or "Record"
        )
        with st.expander(title):
            st.json(record)


def _raw_hash(rows: tuple[dict[str, object], ...]) -> str:
    payload = repr(
        sorted(
            (
                str(row["well_id"]),
                str(row["channel"]),
                row.get("time_index"),
                row.get("elapsed_microseconds"),
                row["value_raw"],
            )
            for row in rows
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def current_background_revision(snapshot: PlateSnapshot) -> str | None:
    revisions = snapshot.revisions
    return next(
        (
            str(row["revision_id"])
            for row in reversed(revisions)
            if row["algorithm_name"] == "growth_background" and bool(row["is_current"])
        ),
        None,
    )


def _store_source(name: str, text: str) -> None:
    if not text.strip():
        raise ValueError("Growth CSV is empty")
    st.session_state.growth_source_name = name
    st.session_state.growth_csv_text = text


def _load_growth_view(context: AppContext, plate_id: PlateId) -> GrowthRunView:
    service = LoadGrowthRunService(context.repository)
    token = service.cache_token(context.actor, plate_id)
    cache = cast(dict[str, tuple[str, GrowthRunView]], st.session_state.setdefault("run_cache", {}))
    cached = cache.get(str(plate_id))
    if cached is not None and cached[0] == token:
        return cached[1]
    view = service.execute(context.actor, plate_id)
    cache[str(plate_id)] = (token, view)
    return view


def _next_step() -> None:
    st.session_state.growth_wizard_step += 1
    st.rerun()


def _previous_step() -> None:
    st.session_state.growth_wizard_step -= 1
    st.rerun()


def _reset_wizard() -> None:
    for key in tuple(st.session_state):
        if str(key).startswith("growth_"):
            del st.session_state[key]


def _clear_growth_plot() -> None:
    for key in (
        "growth_plot",
        "growth_plot_issues",
        "growth_plot_plate_id",
        "growth_plot_title",
        "growth_plot_pdf",
        "growth_plot_pdf_error",
        "growth_plot_csv",
        "growth_plot_wide_csv",
        "growth_plot_styles",
        "growth_plate_overview",
        "growth_plate_overview_issues",
        "growth_plate_overview_plate_id",
    ):
        st.session_state.pop(key, None)


def _invalidate_growth_view(plate_id: PlateId) -> None:
    """Force the next workspace rerun to load the just-saved well layout."""

    cache = st.session_state.get("run_cache")
    if isinstance(cache, dict):
        cache.pop(str(plate_id), None)


def _invalidate_growth_discovery() -> None:
    """Discard cached Library/comparison state after a relevant committed write."""

    for key in (
        "run_search_results",
        "run_library_custom_columns",
        "growth_export_search_results",
        "growth_export_custom_columns",
        "growth_comparison_plate_index_cache",
        "growth_comparison_source_plate_ids",
        "growth_comparison_search_result",
        "growth_comparison_search_source_plate_ids",
        "growth_comparison_search_revision",
        "growth_comparison_basket",
        "growth_comparison_basket_revision",
        "growth_comparison_plot_source_plate_ids",
        "growth_comparison_plot_basket_keys",
        # Remove session state created by the superseded common-condition workflow.
        "growth_comparison_condition_cache",
        "growth_comparison_result",
        "growth_comparison_result_plate_ids",
        "growth_comparison_selected_condition_keys",
        "growth_comparison_selected_result",
        "growth_comparison_plot_result",
        "growth_comparison_plot_plate_ids",
    ):
        st.session_state.pop(key, None)


def _json_mapping(value: object) -> dict[str, object]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON metadata object")
    return {str(key): item for key, item in parsed.items()}


def _noop(*_args: object, **_kwargs: object) -> None:
    return None
