"""Library-scoped shared cultivation metadata editing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import PlateId
from plate_reader.application.services.growth_cultivation_bulk import (
    CULTIVATION_SHARED_FIELDS,
    GrowthCultivationMetadata,
    GrowthCultivationTarget,
    LoadBulkGrowthCultivationMetadataService,
    UpdateBulkGrowthCultivationMetadataService,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_cultivation import CULTIVATION_DESCRIPTION_FIELDS

_RECORDS_KEY = "growth_library_cultivation_records"
_REVISION_KEY = "growth_library_cultivation_revision"
_RESULT_KEY = "growth_library_cultivation_save_result"
_ERROR_KEY = "growth_library_cultivation_error"
_LABELS = {
    "Team_Code": "Team code",
    "CultivationSystemCode": "Cultivation system / experiment code",
    **dict(CULTIVATION_DESCRIPTION_FIELDS),
}


def clear_bulk_cultivation_editor() -> None:
    """Discard the submitted batch when the user searches or chooses another batch."""

    st.session_state.pop(_RECORDS_KEY, None)
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_ERROR_KEY, None)


def open_bulk_cultivation_editor(context: AppContext, plate_ids: Sequence[PlateId]) -> None:
    """Read metadata only after the Library's explicit edit action."""

    clear_bulk_cultivation_editor()
    records = LoadBulkGrowthCultivationMetadataService(context.repository).execute(
        context.actor, plate_ids
    )
    st.session_state[_RECORDS_KEY] = records
    st.session_state[_REVISION_KEY] = int(st.session_state.get(_REVISION_KEY, 0)) + 1


def render_bulk_cultivation_editor(context: AppContext) -> tuple[PlateId, ...] | None:
    """Return changed run IDs on save; None means no successful submission."""

    result = st.session_state.pop(_RESULT_KEY, None)
    if result is not None:
        return cast(tuple[PlateId, ...], result)
    records = cast(tuple[GrowthCultivationMetadata, ...], st.session_state.get(_RECORDS_KEY, ()))
    if not records:
        return None
    revision = int(st.session_state[_REVISION_KEY])
    st.subheader("Shared cultivation metadata")
    st.caption(f"Editing {len(records)} selected run(s).")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Experiment": record.experiment_name,
                    "Plate": record.plate_name,
                    **{
                        _LABELS[field]: record.registry.get(field, "")
                        for field in CULTIVATION_SHARED_FIELDS
                    },
                }
                for record in records
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Choose the shared fields to apply to these runs. Team/system edits set generator "
        "defaults; existing cultivation IDs and per-well overrides retain their saved values."
    )
    fields = st.multiselect(
        "Cultivation fields to update",
        CULTIVATION_SHARED_FIELDS,
        format_func=lambda field: _LABELS[field],
        key=f"cultivation-bulk-fields-{revision}",
    )
    with st.form(f"cultivation-bulk-form-{revision}"):
        for field in fields:
            values = [str(record.registry.get(field) or "") for record in records]
            common_value = values[0] if len(set(values)) == 1 else ""
            st.text_input(
                _LABELS[field],
                value=common_value,
                key=f"cultivation-bulk-value-{revision}-{field}",
                help="Current values differ across the selection."
                if len(set(values)) > 1
                else None,
            )
        st.checkbox(
            "Fill empty values only",
            value=True,
            key=f"cultivation-bulk-fill-{revision}",
            help=(
                "Uncheck to replace the chosen fields in every selected run. "
                "Empty text then clears a field."
            ),
        )
        st.form_submit_button(
            "Save cultivation metadata to selected runs",
            disabled=not fields,
            on_click=_save_bulk_metadata,
            args=(context, records, tuple(fields), revision),
        )
    st.button("Cancel cultivation metadata editing", on_click=clear_bulk_cultivation_editor)
    if error := st.session_state.get(_ERROR_KEY):
        st.error(str(error))
    return None


def _save_bulk_metadata(
    context: AppContext,
    records: Sequence[GrowthCultivationMetadata],
    fields: Sequence[str],
    revision: int,
) -> None:
    """Process the submitted form before rendering, so closed widgets are cleared."""

    st.session_state.pop(_ERROR_KEY, None)
    changes = {
        field: str(st.session_state[f"cultivation-bulk-value-{revision}-{field}"])
        for field in fields
    }
    try:
        st.session_state[_RESULT_KEY] = UpdateBulkGrowthCultivationMetadataService(
            context.repository
        ).execute(
            context.actor,
            tuple(
                GrowthCultivationTarget(record.plate_id, record.updated_at) for record in records
            ),
            changes,
            fill_missing_only=bool(st.session_state[f"cultivation-bulk-fill-{revision}"]),
        )
    except Exception as error:
        st.session_state[_ERROR_KEY] = (
            f"Unable to save shared cultivation metadata: {error}. "
            "Your entries are still here. If a run changed, "
            "reopen the editor to load current values."
        )
