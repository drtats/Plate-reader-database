"""Shared portable SQLite import page for local and hosted modes."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import streamlit as st

from plate_reader.application.contracts import ImportPortableRun, Role
from plate_reader.application.ports import PortableImportPreviewData, PortableImportResultData
from plate_reader.application.services import ImportPortableRunService, PreviewPortableRunService
from plate_reader.infrastructure.database import SqlitePortableRunImporter
from plate_reader.ui.context import AppContext
from plate_reader.ui.pages import render_exception


def render_portable_import(context: AppContext, *, allow_local_path: bool) -> None:
    st.header("Import Portable Data")
    st.caption(
        "Import a checksummed .plate-reader.sqlite export. Preview is read-only; "
        "commit is one transaction."
    )
    upload = st.file_uploader(
        "Portable SQLite export",
        type=("sqlite", "sqlite3", "db"),
        key="portable_import_upload",
    )
    if st.button("Preview selected portable file", type="primary"):
        try:
            if upload is None:
                raise ValueError("Choose a portable SQLite export first")
            _preview_content(context, upload.name, upload.getvalue())
        except Exception as error:
            render_exception(error)
    if allow_local_path:
        with st.expander("Standalone/local: load a portable file path"):
            local_path = st.text_input("Portable file path")
            if st.button("Preview portable local path"):
                try:
                    path = Path(local_path).expanduser()
                    _preview_content(context, path.name, path.read_bytes())
                except Exception as error:
                    render_exception(error)

    preview_value = st.session_state.get("portable_import_preview")
    if not isinstance(preview_value, PortableImportPreviewData):
        return
    preview = preview_value
    st.subheader("Validated portable contents")
    metrics = st.columns(4)
    metrics[0].metric("Plates", len(preview.plate_ids))
    metrics[1].metric("Revisions", len(preview.revision_ids))
    metrics[2].metric("Rows", sum(preview.table_counts.values()))
    metrics[3].metric("ID collisions", sum(preview.collisions.values()))
    st.caption(f"Export {preview.export_id} · SHA-256 {preview.file_sha256}")
    with st.expander("Table counts and collision details"):
        st.json({"table_counts": preview.table_counts, "collisions": preview.collisions})
    if context.actor.role is Role.VIEWER:
        st.info("Viewer accounts may validate exports but cannot import them.")
        return
    collision_choice = st.selectbox(
        "Identifier collision policy",
        ("Remap incoming IDs safely", "Reject every collision"),
    )
    st.warning("Import adds data and provenance. Existing records are never overwritten.")
    if st.button("Import portable data", type="primary"):
        try:
            content = cast(bytes, st.session_state.portable_import_content)
            result = ImportPortableRunService(
                context.repository,
                SqlitePortableRunImporter(context.repository.connection),
            ).execute(
                ImportPortableRun(
                    context.actor,
                    preview.file_sha256,
                    collision_policy=("remap" if collision_choice.startswith("Remap") else "error"),
                    dry_run=False,
                ),
                content,
            )
            if not isinstance(result, PortableImportResultData):
                raise RuntimeError("Portable commit returned preview data")
            st.session_state.portable_import_result = result
            st.session_state.pop("run_library_results", None)
            st.session_state.pop("mic_library_results", None)
            message = (
                f"Imported {len(result.plate_id_map)} plate(s)."
                if result.created
                else "This exact portable export was already imported; no rows were duplicated."
            )
            st.success(message)
        except Exception as error:
            render_exception(error)
    result_value = st.session_state.get("portable_import_result")
    if isinstance(result_value, PortableImportResultData):
        with st.expander("Portable import ID mapping"):
            st.json(
                {
                    "plate_id_map": result_value.plate_id_map,
                    "revision_id_map": result_value.revision_id_map,
                }
            )


def _preview_content(context: AppContext, name: str, content: bytes) -> None:
    preview = PreviewPortableRunService(
        context.repository,
        SqlitePortableRunImporter(context.repository.connection),
    ).execute(context.actor, content)
    st.session_state.portable_import_name = name
    st.session_state.portable_import_content = content
    st.session_state.portable_import_preview = preview
    st.session_state.pop("portable_import_result", None)
