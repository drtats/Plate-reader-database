"""Saved-option suggestions for the shared plate editor."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import AssayType, DeleteOption, Role, SaveOption
from plate_reader.application.services import (
    DeleteLayoutColumnService,
    DeleteOptionService,
    ListLayoutColumnsService,
    ListSavedOptionsService,
    SaveLayoutColumnService,
    SaveOptionService,
)
from plate_reader.ui.context import AppContext


@dataclass(frozen=True, slots=True)
class OptionField:
    option_type: str
    column: str
    label: str


_SHARED_FIELDS = (
    OptionField("strain", "Strain", "Strain"),
    OptionField("medium", "Media", "Media"),
    OptionField("treatment", "Treatment", "Treatment"),
    OptionField("concentration_unit", "Concentration unit", "Concentration unit"),
)
_GROWTH_FIELDS = (
    *_SHARED_FIELDS,
    OptionField("background_group", "Background group", "Background group"),
    OptionField("grouping_label", "Group", "Group"),
    OptionField("inoculum_unit", "Inoculum unit", "Inoculum unit"),
)
_MIC_FIELDS = (
    OptionField("strain", "Strain", "Strain"),
    OptionField("medium", "Media", "Media"),
    OptionField("treatment", "Antibiotic / treatment", "Antibiotic / treatment"),
    OptionField("concentration_unit", "Concentration unit", "Concentration unit"),
)


def option_fields(assay_type: AssayType) -> tuple[OptionField, ...]:
    if assay_type is AssayType.GROWTH:
        return _GROWTH_FIELDS
    if assay_type is AssayType.MIC:
        return _MIC_FIELDS
    raise ValueError(f"Saved options are not supported for {assay_type.value} assays")


def saved_option_suggestions(
    context: AppContext, assay_type: AssayType
) -> dict[str, tuple[str, ...]]:
    """Map backend option records onto assay-specific editor columns."""

    fields = option_fields(assay_type)
    column_by_type = {field.option_type: field.column for field in fields}
    values: dict[str, list[str]] = {field.column: [] for field in fields}
    for option in ListSavedOptionsService(context.repository).execute(context.actor):
        column = column_by_type.get(option.option_type)
        if column is not None:
            values[column].append(option.value)
    return {column: tuple(items) for column, items in values.items() if items}


def layout_custom_column_names(context: AppContext, assay_type: AssayType) -> tuple[str, ...]:
    """Return custom columns shared by every layout of one assay type."""

    return tuple(
        column.name
        for column in ListLayoutColumnsService(context.repository).execute(
            context.actor, assay_type
        )
    )


def save_layout_custom_column(context: AppContext, assay_type: AssayType, name: str) -> None:
    """Persist a layout column so subsequent experiments expose it."""

    SaveLayoutColumnService(context.repository).execute(context.actor, assay_type, name)


def delete_layout_custom_column(context: AppContext, assay_type: AssayType, name: str) -> None:
    """Stop offering a layout column globally without deleting saved well values."""

    DeleteLayoutColumnService(context.repository).execute(context.actor, assay_type, name)


def render_saved_option_controls(
    context: AppContext,
    *,
    assay_type: AssayType,
    frame: pd.DataFrame,
) -> None:  # pragma: no cover - Streamlit widget composition
    """Let administrators maintain suggestions without constraining editor values."""

    if context.actor.role is not Role.ADMIN:
        return
    fields = option_fields(assay_type)
    field_by_type = {field.option_type: field for field in fields}
    control_key = f"saved_options_{assay_type.value}"
    with st.expander("Saved fill suggestions"):
        flash = st.session_state.pop(f"{control_key}_flash", None)
        if flash:
            st.success(str(flash))
        selected_type = st.selectbox(
            "Suggestion field",
            tuple(field_by_type),
            format_func=lambda value: field_by_type[value].label,
            key=f"{control_key}_field",
        )
        field = field_by_type[selected_type]
        try:
            existing = tuple(
                option.value
                for option in ListSavedOptionsService(context.repository).execute(
                    context.actor, selected_type
                )
            )
        except Exception as error:
            st.error(f"Could not load saved suggestions: {error}")
            return
        candidates = tuple(dict.fromkeys((*existing, *_frame_values(frame, field.column))))
        value = st.selectbox(
            "Suggestion value",
            candidates,
            index=None,
            placeholder="Choose an existing layout value or type a new one",
            accept_new_options=True,
            key=f"{control_key}_value_{selected_type}",
        )
        if st.button("Save fill suggestion", key=f"{control_key}_save"):
            try:
                saved = SaveOptionService(context.repository).execute(
                    SaveOption(context.actor, selected_type, str(value or ""))
                )
                st.session_state[f"{control_key}_flash"] = (
                    f"Saved {field.label} suggestion: {saved.value}."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not save fill suggestion: {error}")
        if existing:
            remove = st.selectbox(
                "Saved suggestion to remove",
                existing,
                index=None,
                key=f"{control_key}_remove_{selected_type}",
            )
            confirmed = st.checkbox(
                "Confirm suggestion deletion",
                key=f"{control_key}_confirm_delete",
            )
            if st.button(
                "Delete fill suggestion",
                disabled=remove is None or not confirmed,
                key=f"{control_key}_delete",
            ):
                try:
                    DeleteOptionService(context.repository).execute(
                        DeleteOption(context.actor, selected_type, str(remove))
                    )
                    st.session_state[f"{control_key}_flash"] = (
                        f"Deleted {field.label} suggestion: {remove}."
                    )
                    st.rerun()
                except Exception as error:
                    st.error(f"Could not delete fill suggestion: {error}")


def _frame_values(frame: pd.DataFrame, column: str) -> tuple[str, ...]:
    if column not in frame:
        return ()
    values: list[str] = []
    for value in frame[column]:
        if value is None or bool(pd.isna(value)):
            continue
        text = str(value).strip()
        if text and text not in values:
            values.append(text)
    return tuple(values)
