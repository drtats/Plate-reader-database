"""Run Library, growth import wizard, and run workspace pages."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import cast

import streamlit as st

from plate_reader import __version__
from plate_reader.application.contracts import (
    ComputeGrowthBackgroundRevision,
    ExportPortableRun,
    GrowthRunMetadata,
    ImportGrowthRun,
    LifecycleStatus,
    PlateId,
    RevisionId,
    SearchRuns,
    UpdatePlateMetadata,
    UpdateWellLayout,
    WellLayoutChange,
)
from plate_reader.application.demo import synthetic_growth_csv
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services import (
    ComputeGrowthBackgroundService,
    ExportGrowthRunService,
    GrowthRunView,
    ImportGrowthRunService,
    LoadGrowthRunService,
    PortableArtifact,
    PreviewGrowthRunService,
    SearchGrowthRunsService,
    UpdateGrowthLayoutService,
    UpdateGrowthMetadataService,
)
from plate_reader.domain.growth import (
    GROWTH_BACKGROUND_VERSION,
    GROWTH_NORMALIZATION_VERSION,
    parse_label_layout,
)
from plate_reader.infrastructure.database import SqlitePortableRunExporter
from plate_reader.infrastructure.database.repository import ConcurrencyConflictError
from plate_reader.ui.context import AppContext
from plate_reader.ui.plate_editor import (
    growth_layout_changes,
    growth_layout_frame,
    render_plate_editor,
)
from plate_reader.ui.plotting import endpoint_heatmap, growth_curve_figure

LOGGER = logging.getLogger(__name__)


def render_run_library(context: AppContext) -> None:
    st.header("Run Library")
    with st.form("run-search"):
        text = st.text_input("Search experiment, plate, or project")
        submitted = st.form_submit_button("Search")
    if submitted or "run_search_results" not in st.session_state:
        st.session_state.run_search_results = SearchGrowthRunsService(context.repository).execute(
            SearchRuns(actor=context.actor, text=text)
        )
    results = st.session_state.run_search_results
    if not results:
        st.info("No growth runs yet. Open New Growth Run to import the first plate.")
        return
    for run in results:
        with st.container(border=True):
            st.markdown(f"**{run.experiment_name} — {run.plate_name}**")
            st.caption(
                f"{run.experiment_date} · {run.project or 'No project'} · updated {run.updated_at}"
            )
    labels = {f"{run.experiment_name} — {run.plate_name}": run.plate_id for run in results}
    selected_label = st.selectbox("Open run", tuple(labels))
    if st.button("Open workspace", type="primary"):
        st.session_state.selected_plate_id = labels[selected_label]
        st.session_state.pending_navigation = "Growth Workspace"
        st.session_state.pop("growth_plot", None)
        st.session_state.pop("portable_artifact", None)
        st.rerun()


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
            "Plate name", value=str(saved.get("plate_name", "Plate 1"))
        )
        project = identity_left.text_input("Project", value=str(saved.get("project", "")))
        tags = identity_right.text_input(
            "Tags (comma separated)", value=str(saved.get("tags_text", ""))
        )
        details = st.columns(4)
        experiment_date = details[0].date_input(
            "Date", value=cast(date, saved.get("experiment_date", date.today()))
        )
        operator_name = details[1].text_input("User", value=str(saved.get("operator_name", "")))
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
        )
        temperature = details[3].number_input(
            "Temperature", value=float(str(saved.get("temperature", 37.0))), step=0.1
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
        )
        units = st.columns(2)
        temperature_unit = units[0].text_input(
            "Temperature unit", value=str(saved.get("temperature_unit", "C"))
        )
        measurement_type = units[1].text_input(
            "Measurement type", value=str(saved.get("measurement_type", "OD600"))
        )
        notes = st.text_area("Run notes", value=str(saved.get("notes", "")))
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
                "temperature": float(temperature),
                "temperature_unit": temperature_unit.strip() or None,
                "measurement_type": measurement_type.strip() or None,
                "manual_subtraction": float(subtraction),
                "notes": notes.strip() or None,
            }
            _next_step()
    if st.button("Back", key="metadata-back"):
        _previous_step()


def wizard_layout(_context: AppContext) -> None:
    st.subheader("4. Review the 96-well layout")
    label_text = st.session_state.get("growth_label_csv_text") or None
    labels = (
        {label.position.label: label.label for label in parse_label_layout(label_text)}
        if label_text
        else {}
    )
    frame = render_plate_editor(
        growth_layout_frame(labels), state_key="growth_layout_frame", assay="growth"
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
            st.session_state.pop("run_search_results", None)
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
    analysis_state = (
        f"background revision {current_revision}" if current_revision is not None else "raw only"
    )
    st.caption(f"Plate ID: {plate_id} · Analysis state: {analysis_state}")
    if message := st.session_state.pop("commit_message", None):
        st.success(message)
    tabs = st.tabs(
        ("Overview & QC", "Metadata", "Layout", "Plotting", "Revisions", "Export", "Provenance")
    )
    with tabs[0]:
        render_overview(view)
    with tabs[1]:
        render_metadata_form(context, plate_id, metadata)
    with tabs[2]:
        render_layout_form(context, plate_id, view)
    with tabs[3]:
        render_plotting(view)
    with tabs[4]:
        render_revisions(context, plate_id, view)
    with tabs[5]:
        render_export(context, migrations, plate_id, view)
    with tabs[6]:
        render_records(view.provenance, empty_message="No provenance events are recorded.")


def render_overview(view: GrowthRunView) -> None:
    snapshot = view.snapshot
    raw_hash = _raw_hash(snapshot.raw_observations)
    left, middle, right = st.columns(3)
    left.metric("Wells", len(snapshot.wells))
    middle.metric("Measurements", len(snapshot.raw_observations))
    right.metric("Background rows", len(view.backgrounds))
    if current_background_revision(snapshot) is None:
        st.info("No background revision exists. Plots show immutable raw values.")
    elif not view.backgrounds:
        st.warning("The current revision contains no background rows; check blank assignments.")
    else:
        status_counts: dict[str, int] = {}
        for row in view.backgrounds:
            status = str(row["qc_status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        st.caption(
            "Background QC: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        )
    st.plotly_chart(
        endpoint_heatmap(snapshot.raw_observations, snapshot.wells, raw_hash),
        width="stretch",
    )


def render_metadata_form(
    context: AppContext, plate_id: PlateId, metadata: dict[str, object]
) -> None:
    st.warning("Changes below are unsaved until Save metadata is pressed.")
    with st.form("workspace-metadata"):
        experiment_name = st.text_input("Experiment name", value=str(metadata["name"]))
        plate_name = st.text_input("Plate name", value=str(metadata["plate_name"]))
        project = st.text_input("Project", value=str(metadata["project"] or ""))
        instrument = st.text_input("Instrument", value=str(metadata["instrument"] or ""))
        notes = st.text_area("Notes", value=str(metadata["notes"] or ""))
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
                    instrument=instrument or None,
                    notes=notes or None,
                    lifecycle_status=lifecycle,
                )
            )
            st.success("Metadata saved.")
            st.rerun()
        except Exception as error:
            render_exception(error)


def render_layout_form(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> None:
    st.warning("Well changes are unsaved until Save well is pressed.")
    positions = tuple(str(well["position"]) for well in view.snapshot.wells)
    selected_position = st.selectbox("Well", positions, key=f"layout-position-{plate_id}")
    selected = next(
        well for well in view.snapshot.wells if str(well["position"]) == selected_position
    )
    with st.form(f"workspace-layout-{plate_id}-{selected_position}"):
        display_name = st.text_input("Display name", value=str(selected["display_name"] or ""))
        is_blank = st.checkbox("Blank", value=bool(selected["is_blank"]))
        background_group = st.text_input(
            "Background group", value=str(selected["background_group"] or "plate")
        )
        strain = st.text_input("Strain", value=str(selected["strain"] or ""))
        medium = st.text_input("Medium", value=str(selected["medium"] or ""))
        replicate = st.number_input(
            "Replicate", min_value=1, value=_database_int(selected["replicate"] or 1), step=1
        )
        submitted = st.form_submit_button("Save well", type="primary")
    if submitted:
        try:
            UpdateGrowthLayoutService(context.repository).execute(
                UpdateWellLayout(
                    context.actor,
                    plate_id,
                    str(view.snapshot.metadata["updated_at"]),
                    (
                        WellLayoutChange(
                            position=selected_position,
                            display_name=display_name or None,
                            is_blank=is_blank,
                            background_group=background_group,
                            strain=strain or None,
                            medium=medium or None,
                            replicate=replicate,
                        ),
                    ),
                )
            )
            st.success("Well saved without rewriting measurements.")
            st.rerun()
        except Exception as error:
            render_exception(error)


def render_plotting(view: GrowthRunView) -> None:
    positions = tuple(str(well["position"]) for well in view.snapshot.wells)
    selected = st.multiselect("Wells", positions, default=positions[:8])
    corrected = st.toggle(
        "Apply current background revision",
        value=bool(view.backgrounds),
        disabled=not bool(view.backgrounds),
    )
    if corrected and not view.backgrounds:
        st.warning("No background revision exists; raw values will be shown.")
    if st.button("Render selected curves", type="primary"):
        revision_key = next(
            (
                str(row["revision_id"])
                for row in reversed(view.snapshot.revisions)
                if bool(row["is_current"])
            ),
            "raw",
        )
        raw_hash = _raw_hash(view.snapshot.raw_observations)
        st.session_state.growth_plot = growth_curve_figure(
            view.snapshot.raw_observations,
            view.snapshot.wells,
            view.backgrounds,
            tuple(selected),
            corrected,
            raw_hash,
            revision_key,
        )
        st.session_state.growth_plot_plate_id = str(view.snapshot.plate_id)
    if (figure := st.session_state.get("growth_plot")) is not None and st.session_state.get(
        "growth_plot_plate_id"
    ) == str(view.snapshot.plate_id):
        st.plotly_chart(figure, width="stretch")


def render_revisions(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> None:
    render_records(view.snapshot.revisions, empty_message="No analysis revisions yet.")
    if st.button("Compute new background revision", type="primary"):
        try:
            result = ComputeGrowthBackgroundService(context.repository).execute(
                ComputeGrowthBackgroundRevision(context.actor, plate_id, GROWTH_BACKGROUND_VERSION)
            )
            st.success(
                f"Revision saved with {result.background_count} background rows and "
                f"{len(result.issues)} warning(s)."
            )
            st.rerun()
        except Exception as error:
            render_exception(error)


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


def _noop(*_args: object, **_kwargs: object) -> None:
    return None


def _database_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer database value")
    return value
