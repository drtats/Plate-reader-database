"""Synchronized 96-well selection controls for Growth workflows."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence

import pandas as pd
import streamlit as st

from plate_reader.application.services import (
    GrowthSelectionOperation,
    GrowthWellFilter,
    GrowthWellSelectionService,
    combine_growth_selection,
    growth_selection_fields,
    normalize_growth_selection,
)
from plate_reader.arrow_runtime import configure_arrow_memory_pool
from plate_reader.domain.common.plate import PLATE_96, WellPosition

_OPERATION_LABELS = {
    GrowthSelectionOperation.REPLACE: "Replace selection",
    GrowthSelectionOperation.ADD: "Add matches",
    GrowthSelectionOperation.REMOVE: "Remove matches",
    GrowthSelectionOperation.KEEP_ONLY: "Keep only matches",
}


def render_growth_well_selector(
    wells: Sequence[Mapping[str, object]],
    default_positions: Iterable[str],
    *,
    state_key: str,
) -> tuple[str, ...]:  # pragma: no cover - Streamlit widget composition
    """Render synchronized plate/list/filter controls and return staged positions."""

    configure_arrow_memory_pool()
    if state_key not in st.session_state:
        st.session_state[state_key] = normalize_growth_selection(wells, default_positions)
    selected = normalize_growth_selection(wells, st.session_state[state_key])
    st.session_state[state_key] = selected
    all_positions = tuple(position.label for position in PLATE_96.positions())

    st.subheader("Select wells")
    st.caption(
        "The 96-well grid, list, and metadata filters edit one staged selection. "
        "Plot immediately, or use Save well selection to keep it as this plate's default."
    )
    metric, select_all, clear_all, invert = st.columns((1.4, 1, 1, 1))
    metric.metric("Selected wells", len(selected))
    if select_all.button("Select all", key=f"{state_key}_all"):
        _store_selection(state_key, all_positions)
    if clear_all.button("Clear all", key=f"{state_key}_clear"):
        _store_selection(state_key, ())
    if invert.button("Invert", key=f"{state_key}_invert"):
        inverse = tuple(position for position in all_positions if position not in set(selected))
        _store_selection(state_key, inverse)

    with st.expander("Row and column shortcuts", expanded=False):
        st.caption(
            "When both are supplied, selected rows and columns are combined. Choose how those "
            "matching wells change the staged selection."
        )
        row_column, column_column, operation_column = st.columns(3)
        rows = row_column.multiselect("Rows", tuple("ABCDEFGH"), key=f"{state_key}_shortcut_rows")
        columns = column_column.multiselect(
            "Columns", tuple(range(1, 13)), key=f"{state_key}_shortcut_columns"
        )
        shortcut_operation = operation_column.selectbox(
            "Shortcut operation",
            tuple(GrowthSelectionOperation),
            format_func=lambda value: _OPERATION_LABELS[value],
            key=f"{state_key}_shortcut_operation",
        )
        if st.button(
            "Apply row/column shortcut",
            disabled=not rows and not columns,
            key=f"{state_key}_shortcut_apply",
        ):
            matches = tuple(
                position
                for position in all_positions
                if position[0] in rows or int(position[1:]) in columns
            )
            _store_selection(
                state_key,
                combine_growth_selection(selected, matches, shortcut_operation),
            )

    reference_tab, grid_tab, list_tab, filter_tab = st.tabs(
        ("Reference plate", "96-well selection", "Selection list", "Metadata filters")
    )
    with reference_tab:
        st.markdown(
            growth_selection_reference(wells).to_html(border=0),
            unsafe_allow_html=True,
        )
    with grid_tab:
        if _is_streamlit_test():
            st.caption("Interactive 8x12 selection grid enabled in the running app.")
        else:
            with st.form(f"{state_key}_grid_form"):
                edited_grid = st.data_editor(
                    growth_selection_grid(selected),
                    width="stretch",
                    column_config={
                        str(column): st.column_config.CheckboxColumn(str(column))
                        for column in range(1, 13)
                    },
                    key=f"{state_key}_grid",
                )
                apply_grid = st.form_submit_button("Apply 96-well selection", type="primary")
            if apply_grid:
                _store_selection(state_key, growth_selection_from_grid(edited_grid))
    with list_tab:
        if _is_streamlit_test():
            st.caption("Interactive 96-row selection list enabled in the running app.")
        else:
            with st.form(f"{state_key}_list_form"):
                edited_list = st.data_editor(
                    growth_selection_list(wells, selected),
                    height=500,
                    width="stretch",
                    hide_index=True,
                    disabled=("Well", "Display name", "Raw label"),
                    column_config={"Selected": st.column_config.CheckboxColumn("Selected")},
                    key=f"{state_key}_list",
                )
                apply_list = st.form_submit_button("Apply selection list", type="primary")
            if apply_list:
                _store_selection(state_key, growth_selection_from_list(edited_list))
    with filter_tab:
        fields = growth_selection_fields(wells)
        fields_by_label = {field.label: field for field in fields}
        chosen_labels = st.multiselect(
            "Filter fields",
            tuple(fields_by_label),
            key=f"{state_key}_filter_fields",
        )
        with st.form(f"{state_key}_filter_form"):
            filters = tuple(
                GrowthWellFilter(
                    fields_by_label[label].key,
                    tuple(
                        st.multiselect(
                            f"{label} values",
                            fields_by_label[label].values,
                            key=f"{state_key}_filter_{fields_by_label[label].key}",
                        )
                    ),
                )
                for label in chosen_labels
            )
            filter_operation = st.selectbox(
                "Filter operation",
                tuple(GrowthSelectionOperation),
                format_func=lambda value: _OPERATION_LABELS[value],
                key=f"{state_key}_filter_operation",
            )
            apply_filters = st.form_submit_button(
                "Apply metadata filters",
                type="primary",
                disabled=not chosen_labels,
            )
        if not chosen_labels:
            st.info(
                "Choose one or more metadata fields. An empty filter leaves selection unchanged."
            )
        if apply_filters:
            updated = GrowthWellSelectionService().execute(
                wells,
                selected,
                filters,
                filter_operation,
            )
            _store_selection(state_key, updated)

    return normalize_growth_selection(wells, st.session_state[state_key])


def growth_selection_grid(selected_positions: Iterable[str]) -> pd.DataFrame:
    selected = set(_normalize_positions(selected_positions))
    return pd.DataFrame(
        [[f"{row}{column}" in selected for column in range(1, 13)] for row in "ABCDEFGH"],
        index=list("ABCDEFGH"),
        columns=[str(column) for column in range(1, 13)],
        dtype=bool,
    )


def growth_selection_from_grid(frame: pd.DataFrame) -> tuple[str, ...]:
    expected_rows = tuple("ABCDEFGH")
    expected_columns = tuple(str(column) for column in range(1, 13))
    normalized = frame.copy(deep=True)
    normalized.index = normalized.index.map(str)
    normalized.columns = normalized.columns.map(str)
    if tuple(normalized.index) != expected_rows or tuple(normalized.columns) != expected_columns:
        raise ValueError("Growth selection grid must contain rows A-H and columns 1-12")
    return tuple(
        f"{row}{column}"
        for row in expected_rows
        for column in expected_columns
        if _boolean(normalized.loc[row, column])
    )


def growth_selection_list(
    wells: Sequence[Mapping[str, object]], selected_positions: Iterable[str]
) -> pd.DataFrame:
    selected = set(normalize_growth_selection(wells, selected_positions))
    by_position = _wells_by_position(wells)
    return pd.DataFrame.from_records(
        [
            {
                "Well": position.label,
                "Display name": str(by_position[position.label].get("display_name") or ""),
                "Raw label": str(by_position[position.label].get("raw_label") or ""),
                "Selected": position.label in selected,
            }
            for position in PLATE_96.positions()
        ]
    )


def growth_selection_from_list(frame: pd.DataFrame) -> tuple[str, ...]:
    required = {"Well", "Selected"}
    if not required.issubset(frame.columns):
        raise ValueError("Growth selection list requires Well and Selected columns")
    positions: list[str] = []
    seen: set[str] = set()
    for row in frame.to_dict(orient="records"):
        position = WellPosition.parse(str(row["Well"])).label
        if position in seen:
            raise ValueError(f"Duplicate Growth selection well: {position}")
        seen.add(position)
        if _boolean(row["Selected"]):
            positions.append(position)
    expected = {position.label for position in PLATE_96.positions()}
    if seen != expected:
        raise ValueError("Growth selection list must contain every A1-H12 well")
    return _normalize_positions(positions)


def growth_selection_reference(wells: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    by_position = _wells_by_position(wells)
    return pd.DataFrame(
        [
            [
                _well_label(by_position[f"{row}{column}"], f"{row}{column}")
                for column in range(1, 13)
            ]
            for row in "ABCDEFGH"
        ],
        index=list("ABCDEFGH"),
        columns=[str(column) for column in range(1, 13)],
    )


def _store_selection(state_key: str, positions: Iterable[str]) -> None:
    st.session_state[state_key] = tuple(positions)
    st.rerun()


def _wells_by_position(
    wells: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    normalize_growth_selection(wells, ())
    return {WellPosition.parse(str(well.get("position", ""))).label: well for well in wells}


def _normalize_positions(positions: Iterable[str]) -> tuple[str, ...]:
    selected = {WellPosition.parse(str(position)).label for position in positions}
    return tuple(position.label for position in PLATE_96.positions() if position.label in selected)


def _well_label(well: Mapping[str, object], position: str) -> str:
    for field in ("display_name", "raw_label"):
        value = str(well.get(field) or "").strip()
        if value:
            return value
    return position


def _boolean(value: object) -> bool:
    if pd.isna(value):
        return False
    return bool(value)


def _is_streamlit_test() -> bool:  # pragma: no cover - AppTest compatibility
    return os.environ.get("PLATE_READER_ENV", "").casefold() == "test"
