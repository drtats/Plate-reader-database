"""MIC plate library, import wizard, workspace, search, and visualization pages."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import cast

import streamlit as st

from plate_reader import __version__
from plate_reader.application.contracts import (
    AssayType,
    ComputeMicRevision,
    ExportPortableRun,
    ImportMicPlate,
    LifecycleStatus,
    MicExperimentMetadata,
    MicWellLayoutChange,
    PlateId,
    RevisionId,
    Role,
    SearchMicResults,
    SearchRuns,
    SetMicLockState,
    SetMicReviewState,
    SoftDeleteMicPlate,
    UpdateMicLayout,
    UpdateMicMetadata,
)
from plate_reader.application.demo import synthetic_mic_csv
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services import (
    ComputeMicRevisionService,
    ExportMicPlateService,
    ImportMicPlateService,
    LoadMicPlateService,
    MicPlateView,
    PortableArtifact,
    PreviewMicPlateService,
    SearchMicPlatesService,
    SearchMicResultsService,
    SetMicLockStateService,
    SetMicReviewStateService,
    SoftDeleteMicPlateService,
    UpdateMicLayoutService,
    UpdateMicMetadataService,
)
from plate_reader.domain.mic import (
    MIC_ENDPOINT_VERSION,
    MIC_PLATE_PARSER_VERSION,
    parse_mic_plate_csv,
)
from plate_reader.infrastructure.database import SqlitePortableRunExporter
from plate_reader.ui.context import AppContext
from plate_reader.ui.option_controls import (
    render_saved_option_controls,
    saved_option_suggestions,
)
from plate_reader.ui.pages import render_exception, render_records
from plate_reader.ui.plate_editor import (
    mic_layout_changes,
    mic_layout_frame,
    mic_layout_frame_from_snapshot,
    render_plate_editor,
)
from plate_reader.ui.plotting import mic_growth_map, mic_plate_heatmap, mic_result_dot_plot
from plate_reader.ui.template_controls import render_plate_template_controls


def render_mic_library(context: AppContext) -> None:
    st.header("MIC Plate Library")
    with st.form("mic-library-search"):
        text = st.text_input("Search MIC experiment, plate, or project")
        submitted = st.form_submit_button("Search MIC plates")
    if submitted:
        st.session_state.mic_library_offset = 0
    offset = int(st.session_state.setdefault("mic_library_offset", 0))
    if submitted or "mic_library_results" not in st.session_state:
        st.session_state.mic_library_results = SearchMicPlatesService(context.repository).execute(
            SearchRuns(context.actor, text=text, limit=25, offset=offset)
        )
    results = st.session_state.mic_library_results
    if not results:
        st.info("No MIC plates match this page. Open New MIC Plate to add one.")
    else:
        for run in results:
            with st.container(border=True):
                st.markdown(f"**{run.experiment_name} — {run.plate_name}**")
                st.caption(f"{run.experiment_date} · updated {run.updated_at}")
        labels = {f"{run.experiment_name} — {run.plate_name}": run.plate_id for run in results}
        selected = st.selectbox("Open MIC plate", tuple(labels))
        if st.button("Open MIC workspace", type="primary"):
            st.session_state.selected_mic_plate_id = labels[selected]
            st.session_state.pending_navigation = "MIC Workspace"
            _clear_mic_cached_artifacts()
            st.rerun()
    previous, next_page = st.columns(2)
    if previous.button("Previous page", disabled=offset == 0):
        st.session_state.mic_library_offset = max(0, offset - 25)
        st.session_state.pop("mic_library_results", None)
        st.rerun()
    if next_page.button("Next page", disabled=len(results) < 25):
        st.session_state.mic_library_offset = offset + 25
        st.session_state.pop("mic_library_results", None)
        st.rerun()


def render_mic_wizard(context: AppContext, *, allow_local_path: bool) -> None:
    st.header("New MIC Plate")
    step = int(st.session_state.setdefault("mic_wizard_step", 1))
    st.progress(step / 5, text=f"Step {step} of 5")
    if step == 1:
        _mic_source_step(allow_local_path)
    elif step == 2:
        _mic_preview_step()
    elif step == 3:
        _mic_metadata_step()
    elif step == 4:
        _mic_layout_step(context)
    else:
        _mic_commit_step(context)


def _mic_source_step(allow_local_path: bool) -> None:
    st.subheader("1. Choose endpoint plate data")
    upload = st.file_uploader("MIC long-format CSV", type=("csv", "txt"), key="mic_upload")
    pasted = st.text_area("Or paste long-format CSV", key="mic_pasted_csv", height=140)
    use_upload, use_demo = st.columns(2)
    if use_demo.button("Use synthetic MIC demo"):
        _store_mic_source("synthetic-mic.csv", synthetic_mic_csv())
        _mic_next()
    if use_upload.button("Use selected MIC source", type="primary"):
        try:
            if upload is not None:
                _store_mic_source(upload.name, upload.getvalue().decode("utf-8-sig"))
            elif pasted.strip():
                _store_mic_source("pasted-mic.csv", pasted)
            else:
                raise ValueError("Choose a MIC file or paste CSV text")
            _mic_next()
        except Exception as error:
            render_exception(error)
    if allow_local_path:
        with st.expander("Power user: load a configured local MIC path"):
            path_value = st.text_input("Local MIC CSV path")
            if st.button("Load MIC local path"):
                try:
                    path = Path(path_value).expanduser()
                    _store_mic_source(path.name, path.read_text(encoding="utf-8"))
                    _mic_next()
                except Exception as error:
                    render_exception(error)


def _mic_preview_step() -> None:
    st.subheader("2. Validate and calculate")
    threshold = st.number_input("Growth threshold (OD)", min_value=0.0, value=0.1, format="%.4f")
    if st.button("Validate MIC plate", type="primary"):
        try:
            preview = PreviewMicPlateService().execute(st.session_state.mic_csv_text, threshold)
            st.session_state.mic_preview = preview
            st.session_state.mic_threshold = threshold
            _mic_next()
        except Exception as error:
            render_exception(error)
    if st.button("Back", key="mic-preview-back"):
        _mic_previous()


def _mic_metadata_step() -> None:
    st.subheader("3. Describe the MIC experiment")
    preview = st.session_state.mic_preview
    columns = st.columns(4)
    columns[0].metric("Wells", preview.well_count)
    columns[1].metric("Blanks", preview.blank_count)
    columns[2].metric("MIC groups", preview.group_count)
    columns[3].metric("Background", f"{preview.background_value:.4f}")
    saved = cast(dict[str, object], st.session_state.get("mic_metadata", {}))
    with st.form("mic-metadata"):
        identity = st.columns(2)
        experiment_name = identity[0].text_input(
            "MIC experiment name",
            value=str(saved.get("experiment_name", "")),
            key="mic_metadata_experiment_name",
        )
        plate_name = identity[1].text_input(
            "Plate name",
            value=str(saved.get("plate_name", "MIC Plate 1")),
            key="mic_metadata_plate_name",
        )
        details = st.columns(4)
        experiment_date = details[0].date_input(
            "Date", value=cast(date, saved.get("experiment_date", date.today()))
        )
        operator_name = details[1].text_input(
            "Person", value=str(saved.get("operator_name", "")), key="mic_metadata_person"
        )
        reader = details[2].text_input(
            "Reader used", value=str(saved.get("reader", "")), key="mic_metadata_reader"
        )
        incubation_time = details[3].number_input(
            "Incubation time (hrs)",
            min_value=0.0,
            value=float(str(saved.get("incubation_time_hours", 0.0))),
            step=0.5,
        )
        culture = st.columns(4)
        threshold = culture[0].number_input(
            "Threshold (OD)",
            min_value=0.0,
            value=float(str(saved.get("threshold", st.session_state.mic_threshold))),
            format="%.4f",
        )
        inoculum_od = culture[1].number_input(
            "Inoculum OD", value=float(str(saved.get("inoculum_od", 0.0))), format="%.4f"
        )
        growth_phases = ("Lag", "Exponential", "Stationary", "Custom")
        saved_phase = str(saved.get("growth_phase", "Exponential"))
        growth_phase = culture[2].selectbox(
            "Growth phase",
            growth_phases,
            index=growth_phases.index(saved_phase) if saved_phase in growth_phases else 1,
        )
        harvest_od = culture[3].number_input(
            "Harvest OD", value=float(str(saved.get("harvest_od", 0.0))), format="%.4f"
        )
        doubling_time = st.number_input(
            "Doubling time (min)",
            min_value=0.0,
            value=float(str(saved.get("doubling_time_minutes", 0.0))),
            step=1.0,
        )
        notes = st.text_area("Notes", value=str(saved.get("notes", "")), key="mic_metadata_notes")
        submitted = st.form_submit_button("Save MIC metadata and continue")
    if submitted:
        if not experiment_name.strip() or not plate_name.strip():
            st.error("MIC experiment and plate names are required.")
        else:
            st.session_state.mic_metadata = {
                "experiment_name": experiment_name.strip(),
                "plate_name": plate_name.strip(),
                "experiment_date": experiment_date,
                "operator_name": operator_name.strip() or None,
                "reader": reader.strip() or None,
                "incubation_time_hours": float(incubation_time),
                "threshold": float(threshold),
                "inoculum_od": float(inoculum_od),
                "growth_phase": growth_phase,
                "harvest_od": float(harvest_od),
                "doubling_time_minutes": float(doubling_time),
                "notes": notes.strip() or None,
            }
            st.session_state.mic_threshold = float(threshold)
            _mic_next()
    if st.button("Back", key="mic-metadata-back"):
        _mic_previous()


def _mic_layout_step(context: AppContext) -> None:
    st.subheader("4. Review and optionally edit the layout")
    if os.environ.get("PLATE_READER_ENV", "").casefold() == "test":
        # AppTest retains the prior form nodes for one turn; keep their keyed state alive.
        metadata = cast(dict[str, object], st.session_state.mic_metadata)
        with st.expander("Saved metadata", expanded=False):
            st.text_input(
                "MIC experiment name",
                value=str(metadata["experiment_name"]),
                key="mic_metadata_experiment_name",
            )
            st.text_input(
                "Plate name",
                value=str(metadata["plate_name"]),
                key="mic_metadata_plate_name",
            )
            st.text_input(
                "Person",
                value=str(metadata["operator_name"] or ""),
                key="mic_metadata_person",
            )
            st.text_input(
                "Reader used",
                value=str(metadata["reader"] or ""),
                key="mic_metadata_reader",
            )
            st.text_area("Notes", value=str(metadata["notes"] or ""), key="mic_metadata_notes")
    wells = parse_mic_plate_csv(st.session_state.mic_csv_text)
    frame = render_plate_editor(
        mic_layout_frame(wells),
        state_key="mic_layout_frame",
        assay="mic",
        suggestions=saved_option_suggestions(context, AssayType.MIC),
    )
    render_plate_template_controls(
        context,
        assay_type=AssayType.MIC,
        frame=frame,
        state_key="mic_layout_frame",
    )
    render_saved_option_controls(
        context,
        assay_type=AssayType.MIC,
        frame=frame,
    )
    if st.button("Accept MIC layout and continue", type="primary"):
        try:
            changes = mic_layout_changes(frame)
            st.session_state.mic_layout_changes = {change.position: change for change in changes}
            _mic_next()
        except Exception as error:
            render_exception(error)
    if st.button("Back", key="mic-layout-back"):
        _mic_previous()


def _mic_commit_step(context: AppContext) -> None:
    st.subheader("5. Review and commit")
    if os.environ.get("PLATE_READER_ENV", "").casefold() == "test":
        # Keep keyed admin controls alive across AppTest's immediate step transition.
        render_saved_option_controls(
            context,
            assay_type=AssayType.MIC,
            frame=st.session_state.mic_layout_frame,
        )
    metadata = st.session_state.mic_metadata
    preview = st.session_state.mic_preview
    staged = cast(dict[str, MicWellLayoutChange], st.session_state.get("mic_layout_changes", {}))
    st.write(
        f"**{metadata['experiment_name']} — {metadata['plate_name']}**  \n"
        f"{preview.well_count} wells, {preview.group_count} MIC groups, "
        f"{len(staged)} staged layout edit(s)"
    )
    st.info("Raw OD, layout, initial MIC revision, and provenance commit in one transaction.")
    if st.button("Commit MIC plate", type="primary"):
        try:
            result = ImportMicPlateService(context.repository).execute(
                ImportMicPlate(
                    actor=context.actor,
                    source_name=st.session_state.mic_source_name,
                    source_sha256=preview.source_sha256,
                    parser_version=MIC_PLATE_PARSER_VERSION,
                    experiment_name=str(metadata["experiment_name"]),
                    plate_name=str(metadata["plate_name"]),
                    experiment_date=cast(date, metadata["experiment_date"]),
                    threshold=float(st.session_state.mic_threshold),
                ),
                st.session_state.mic_csv_text,
                metadata=MicExperimentMetadata(
                    operator_name=cast(str | None, metadata["operator_name"]),
                    reader=cast(str | None, metadata["reader"]),
                    incubation_time_hours=float(metadata["incubation_time_hours"]),
                    inoculum_od=float(metadata["inoculum_od"]),
                    growth_phase=str(metadata["growth_phase"]),
                    harvest_od=float(metadata["harvest_od"]),
                    doubling_time_minutes=float(metadata["doubling_time_minutes"]),
                    notes=cast(str | None, metadata["notes"]),
                ),
                layout_changes=tuple(staged.values()),
            )
            st.session_state.selected_mic_plate_id = result.plate_id
            st.session_state.pending_navigation = "MIC Workspace"
            st.session_state.mic_commit_message = (
                "MIC plate committed successfully."
                if result.created
                else "This exact MIC source already exists; the stored plate was opened."
            )
            _reset_mic_wizard()
            _clear_mic_cached_artifacts()
            st.rerun()
        except Exception as error:
            render_exception(error)
    if st.button("Back", key="mic-commit-back"):
        _mic_previous()


def render_mic_workspace(context: AppContext, migrations: Path) -> None:
    plate_value = st.session_state.get("selected_mic_plate_id")
    if not plate_value:
        st.info("Choose a MIC plate from the MIC Plate Library first.")
        return
    plate_id = PlateId(str(plate_value))
    try:
        view = _load_mic_view(context, plate_id)
    except Exception as error:
        render_exception(error)
        return
    metadata = view.snapshot.metadata
    st.header(f"{metadata['name']} — {metadata['plate_name']}")
    st.caption(
        f"MIC plate · threshold {metadata['threshold']} · "
        f"{'checked' if metadata['is_checked'] else 'not checked'} · "
        f"{'locked' if metadata['is_locked'] else 'unlocked'}"
    )
    if message := st.session_state.pop("mic_commit_message", None):
        st.success(message)
    tabs = st.tabs(
        (
            "Overview",
            "Metadata",
            "Layout",
            "MIC Results",
            "Revisions",
            "Review & Lifecycle",
            "Export",
            "Provenance",
        )
    )
    with tabs[0]:
        _render_mic_overview(view)
    with tabs[1]:
        _render_mic_metadata(context, plate_id, view)
    with tabs[2]:
        _render_mic_layout(context, plate_id, view)
    with tabs[3]:
        _render_mic_results(view.results)
    with tabs[4]:
        _render_mic_revisions(context, plate_id, view)
    with tabs[5]:
        _render_mic_lifecycle(context, plate_id, view)
    with tabs[6]:
        _render_mic_export(context, migrations, plate_id, view)
    with tabs[7]:
        render_records(view.provenance, empty_message="No MIC provenance events yet.")


def _render_mic_overview(view: MicPlateView) -> None:
    snapshot = view.snapshot
    metrics = st.columns(4)
    metrics[0].metric("Wells", len(snapshot.wells))
    metrics[1].metric("Raw readings", len(snapshot.raw_observations))
    metrics[2].metric("MIC groups", len(view.results))
    metrics[3].metric("Warnings", sum(bool(row.get("warning")) for row in view.results))
    raw_hash = _mic_raw_hash(snapshot.raw_observations)
    left, right = st.columns(2)
    left.plotly_chart(
        mic_plate_heatmap(snapshot.raw_observations, snapshot.wells, raw_hash), width="stretch"
    )
    revision_key = _current_revision_key(snapshot)
    right.plotly_chart(
        mic_growth_map(snapshot.wells, view.well_calls, revision_key), width="stretch"
    )


def _render_mic_metadata(context: AppContext, plate_id: PlateId, view: MicPlateView) -> None:
    metadata = view.snapshot.metadata
    st.warning("Changes are unsaved until Save MIC metadata is pressed.")
    with st.form(f"mic-metadata-{plate_id}"):
        identity = st.columns(2)
        experiment_name = identity[0].text_input("MIC experiment name", value=str(metadata["name"]))
        plate_name = identity[1].text_input("MIC plate name", value=str(metadata["plate_name"]))
        project = identity[0].text_input("MIC project", value=str(metadata["project"] or ""))
        tags = identity[1].text_input(
            "MIC tags (comma separated)",
            value=", ".join(cast(tuple[str, ...], metadata["tags"])),
        )
        details = st.columns(4)
        experiment_date = details[0].date_input(
            "MIC date", value=date.fromisoformat(str(metadata["experiment_date"]))
        )
        operator_name = details[1].text_input(
            "MIC person", value=str(metadata["operator_name"] or "")
        )
        reader = details[2].text_input("MIC reader", value=str(metadata["reader"] or ""))
        incubation_time = details[3].number_input(
            "MIC incubation time (hrs)",
            min_value=0.0,
            value=_optional_database_float(metadata["incubation_time_hours"]),
            step=0.5,
        )
        culture = st.columns(4)
        threshold = culture[0].number_input(
            "MIC threshold",
            min_value=0.0,
            value=_database_float(metadata["threshold"]),
            format="%.4f",
        )
        inoculum_od = culture[1].number_input(
            "MIC inoculum OD",
            value=_optional_database_float(metadata["inoculum_od"]),
            format="%.4f",
        )
        growth_phases = ("Lag", "Exponential", "Stationary", "Custom")
        saved_phase = str(metadata["growth_phase"] or "Exponential")
        growth_phase = culture[2].selectbox(
            "MIC growth phase",
            growth_phases,
            index=growth_phases.index(saved_phase) if saved_phase in growth_phases else 3,
        )
        harvest_od = culture[3].number_input(
            "MIC harvest OD",
            value=_optional_database_float(metadata["harvest_od"]),
            format="%.4f",
        )
        final_details = st.columns(2)
        doubling_time = final_details[0].number_input(
            "MIC doubling time (min)",
            min_value=0.0,
            value=_optional_database_float(metadata["doubling_time_minutes"]),
            step=1.0,
        )
        instrument = final_details[1].text_input(
            "MIC instrument", value=str(metadata["instrument"] or "")
        )
        notes = st.text_area("MIC experiment notes", value=str(metadata["notes"] or ""))
        lifecycle = st.selectbox(
            "MIC lifecycle",
            tuple(LifecycleStatus),
            index=tuple(LifecycleStatus).index(LifecycleStatus(str(metadata["lifecycle_status"]))),
        )
        submitted = st.form_submit_button("Save MIC metadata", type="primary")
    if submitted:
        try:
            UpdateMicMetadataService(context.repository).execute(
                UpdateMicMetadata(
                    context.actor,
                    plate_id,
                    str(metadata["updated_at"]),
                    experiment_name=experiment_name,
                    plate_name=plate_name,
                    project=project or None,
                    experiment_date=experiment_date,
                    tags=tuple(tag.strip() for tag in tags.split(",") if tag.strip()),
                    operator_name=operator_name.strip() or None,
                    reader=reader.strip() or None,
                    incubation_time_hours=float(incubation_time),
                    inoculum_od=float(inoculum_od),
                    growth_phase=growth_phase,
                    harvest_od=float(harvest_od),
                    doubling_time_minutes=float(doubling_time),
                    instrument=instrument.strip() or None,
                    notes=notes.strip() or None,
                    threshold=threshold,
                    lifecycle_status=lifecycle,
                )
            )
            _clear_mic_cached_artifacts()
            st.success("MIC metadata and threshold revision saved.")
            st.rerun()
        except Exception as error:
            render_exception(error)


def _render_mic_layout(context: AppContext, plate_id: PlateId, view: MicPlateView) -> None:
    st.warning("MIC well changes are staged until Save full MIC layout is pressed.")
    state_key = f"workspace_mic_layout_{plate_id}"
    source_key = f"{state_key}_source_updated_at"
    source_updated_at = str(view.snapshot.metadata["updated_at"])
    if st.session_state.get(source_key) != source_updated_at:
        st.session_state[state_key] = mic_layout_frame_from_snapshot(
            view.snapshot.wells, view.snapshot.raw_observations
        )
        st.session_state[f"{state_key}_revision"] = 0
        st.session_state[source_key] = source_updated_at
    frame = render_plate_editor(
        mic_layout_frame_from_snapshot(view.snapshot.wells, view.snapshot.raw_observations),
        state_key=state_key,
        assay="mic",
        immutable_columns=("Raw OD",),
        suggestions=saved_option_suggestions(context, AssayType.MIC),
    )
    render_plate_template_controls(
        context,
        assay_type=AssayType.MIC,
        frame=frame,
        state_key=state_key,
    )
    render_saved_option_controls(
        context,
        assay_type=AssayType.MIC,
        frame=frame,
    )
    if st.button("Save full MIC layout", type="primary"):
        try:
            UpdateMicLayoutService(context.repository).execute(
                UpdateMicLayout(
                    context.actor,
                    plate_id,
                    source_updated_at,
                    mic_layout_changes(frame, include_raw=False),
                )
            )
            _clear_mic_cached_artifacts()
            st.success("Full MIC layout saved and a new analysis revision created.")
            st.rerun()
        except Exception as error:
            render_exception(error)


def _render_mic_results(results: tuple[dict[str, object], ...]) -> None:
    if not results:
        st.info("No MIC result groups exist for this revision.")
        return
    for result in results:
        with st.container(border=True):
            st.markdown(
                f"**{result['strain']} · {result['treatment']} · replicate {result['replicate']}**"
            )
            st.metric(
                "MIC",
                f"{result['mic_operator']} {result['mic_value']} {result['mic_unit']}",
            )
            if result.get("warning"):
                st.warning(str(result["warning"]))


def _render_mic_revisions(context: AppContext, plate_id: PlateId, view: MicPlateView) -> None:
    render_records(view.snapshot.revisions, empty_message="No MIC analysis revisions yet.")
    threshold = st.number_input(
        "New revision threshold",
        min_value=0.0,
        value=_database_float(view.snapshot.metadata["threshold"]),
        format="%.4f",
    )
    if st.button("Compute MIC revision", type="primary"):
        try:
            result = ComputeMicRevisionService(context.repository).execute(
                ComputeMicRevision(
                    context.actor,
                    plate_id,
                    MIC_ENDPOINT_VERSION,
                    threshold,
                )
            )
            _clear_mic_cached_artifacts()
            st.success(f"Saved MIC revision with {result.result_count} group(s).")
            st.rerun()
        except Exception as error:
            render_exception(error)


def _render_mic_lifecycle(context: AppContext, plate_id: PlateId, view: MicPlateView) -> None:
    metadata = view.snapshot.metadata
    checked = st.checkbox("MIC manually checked", value=bool(metadata["is_checked"]))
    if st.button("Save MIC review state"):
        try:
            SetMicReviewStateService(context.repository).execute(
                SetMicReviewState(context.actor, plate_id, str(metadata["updated_at"]), checked)
            )
            _clear_mic_cached_artifacts()
            st.rerun()
        except Exception as error:
            render_exception(error)
    if context.actor.role is Role.ADMIN:
        locked = st.checkbox("Locked from deletion", value=bool(metadata["is_locked"]))
        if st.button("Save MIC lock state"):
            try:
                SetMicLockStateService(context.repository).execute(
                    SetMicLockState(context.actor, plate_id, str(metadata["updated_at"]), locked)
                )
                _clear_mic_cached_artifacts()
                st.rerun()
            except Exception as error:
                render_exception(error)
        with st.expander("Soft-delete MIC plate"):
            st.warning("Soft delete hides the plate but keeps all immutable data.")
            if st.button("Confirm soft delete MIC plate", disabled=bool(metadata["is_locked"])):
                try:
                    SoftDeleteMicPlateService(context.repository).execute(
                        SoftDeleteMicPlate(context.actor, plate_id, str(metadata["updated_at"]))
                    )
                    st.session_state.pop("selected_mic_plate_id", None)
                    st.session_state.pending_navigation = "MIC Plate Library"
                    _clear_mic_cached_artifacts()
                    st.rerun()
                except Exception as error:
                    render_exception(error)


def _render_mic_export(
    context: AppContext, migrations: Path, plate_id: PlateId, view: MicPlateView
) -> None:
    revisions = tuple(
        RevisionId(str(row["revision_id"]))
        for row in view.snapshot.revisions
        if bool(row["is_current"])
    )
    if st.button("Prepare MIC portable export", type="primary"):
        try:
            artifact = ExportMicPlateService(
                context.repository,
                SqlitePortableRunExporter(
                    context.repository.connection,
                    migrations,
                    exporter_version=f"plate-reader/{__version__}",
                ),
            ).execute(ExportPortableRun(context.actor, (plate_id,), revisions))
            st.session_state.mic_portable_artifact = artifact
        except Exception as error:
            render_exception(error)
    if artifact_value := st.session_state.get("mic_portable_artifact"):
        artifact = cast(PortableArtifact, artifact_value)
        st.download_button(
            "Download MIC portable SQLite",
            data=artifact.content,
            file_name=artifact.filename,
            mime="application/vnd.sqlite3",
        )


def render_mic_results_search(context: AppContext) -> None:
    st.header("MIC Results")
    with st.form("mic-results-search"):
        text = st.text_input("Search MIC results")
        strain = st.text_input("Filter strain")
        treatment = st.text_input("Filter treatment")
        medium = st.text_input("Filter medium")
        submitted = st.form_submit_button("Search MIC results")
    if submitted:
        st.session_state.mic_results_offset = 0
    offset = int(st.session_state.setdefault("mic_results_offset", 0))
    if submitted or "mic_result_rows" not in st.session_state:
        st.session_state.mic_result_rows = SearchMicResultsService(context.repository).execute(
            SearchMicResults(
                context.actor,
                strain=strain or None,
                treatment=treatment or None,
                medium=medium or None,
                text=text,
                limit=50,
                offset=offset,
            )
        )
    results = st.session_state.mic_result_rows
    _render_mic_results(results)
    if results:
        result_key = hashlib.sha256(repr(results).encode()).hexdigest()
        if st.button("Render MIC dot plot", type="primary"):
            st.session_state.mic_result_plot = mic_result_dot_plot(results, result_key)
        if figure := st.session_state.get("mic_result_plot"):
            st.plotly_chart(figure, width="stretch")
    previous, next_page = st.columns(2)
    if previous.button("Previous MIC results", disabled=offset == 0):
        st.session_state.mic_results_offset = max(0, offset - 50)
        st.session_state.pop("mic_result_rows", None)
        st.rerun()
    if next_page.button("Next MIC results", disabled=len(results) < 50):
        st.session_state.mic_results_offset = offset + 50
        st.session_state.pop("mic_result_rows", None)
        st.rerun()


def _load_mic_view(context: AppContext, plate_id: PlateId) -> MicPlateView:
    service = LoadMicPlateService(context.repository)
    token = service.cache_token(context.actor, plate_id)
    cache = cast(
        dict[str, tuple[str, MicPlateView]], st.session_state.setdefault("mic_plate_cache", {})
    )
    cached = cache.get(str(plate_id))
    if cached is not None and cached[0] == token:
        return cached[1]
    view = service.execute(context.actor, plate_id)
    cache[str(plate_id)] = (token, view)
    return view


def _current_revision_key(snapshot: PlateSnapshot) -> str:
    revisions = snapshot.revisions
    return next(
        (
            str(row["revision_id"])
            for row in reversed(revisions)
            if row["algorithm_name"] == "mic_endpoint" and bool(row["is_current"])
        ),
        "none",
    )


def _mic_raw_hash(rows: tuple[dict[str, object], ...]) -> str:
    payload = sorted((str(row["well_id"]), str(row["channel"]), row["value_raw"]) for row in rows)
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _store_mic_source(name: str, text: str) -> None:
    if not text.strip():
        raise ValueError("MIC CSV is empty")
    st.session_state.mic_source_name = name
    st.session_state.mic_csv_text = text


def _mic_next() -> None:
    st.session_state.mic_wizard_step += 1
    st.rerun()


def _mic_previous() -> None:
    st.session_state.mic_wizard_step -= 1
    st.rerun()


def _reset_mic_wizard() -> None:
    for key in tuple(st.session_state):
        if str(key).startswith("mic_") and key not in {
            "mic_commit_message",
            "mic_plate_cache",
        }:
            del st.session_state[key]


def _clear_mic_cached_artifacts() -> None:
    st.session_state.pop("mic_plate_cache", None)
    st.session_state.pop("mic_library_results", None)
    st.session_state.pop("mic_result_rows", None)
    st.session_state.pop("mic_result_plot", None)
    st.session_state.pop("mic_portable_artifact", None)


def _database_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expected a numeric database value")
    return float(value)


def _optional_database_float(value: object) -> float:
    return 0.0 if value is None else _database_float(value)
