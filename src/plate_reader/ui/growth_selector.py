"""Synchronized 96-well selection controls for Growth workflows."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence

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
_REFERENCE_FIELDS = (
    ("position", "Well position"),
    ("display_name", "Display name"),
    ("raw_label", "Raw label"),
    ("strain", "Strain"),
    ("medium", "Media"),
    ("treatment", "Treatment"),
    ("concentration", "Concentration"),
    ("concentration_unit", "Concentration unit"),
    ("replicate", "Replicate"),
    ("background_group", "Background group"),
    ("grouping_label", "Group"),
    ("inoculum_size", "Inoculum size"),
    ("inoculum_unit", "Inoculum unit"),
    ("is_blank", "Blank"),
    ("notes", "Notes"),
)


def render_growth_well_selector[FormResultT](
    wells: Sequence[Mapping[str, object]],
    default_positions: Iterable[str],
    *,
    state_key: str,
    form_key: str,
    render_form_controls: Callable[[tuple[str, ...]], FormResultT],
    selection_submitted: Callable[[FormResultT], bool],
) -> tuple[tuple[str, ...], FormResultT]:  # pragma: no cover - Streamlit composition
    """Render selection controls and batch direct grid edits with plot submission."""

    configure_arrow_memory_pool()
    if state_key not in st.session_state:
        st.session_state[state_key] = normalize_growth_selection(wells, default_positions)
    selected = normalize_growth_selection(wells, st.session_state[state_key])
    st.session_state[state_key] = selected
    all_positions = tuple(position.label for position in PLATE_96.positions())

    st.subheader("Select wells")
    st.caption(
        "Check wells in the 96-well grid without rerunning the page. Render selected curves "
        "uses the complete grid once. The selection is kept only for this browser session."
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

    reference_fields = reference_plate_fields(wells)
    reference_keys_by_label = {label: key for key, label in reference_fields}
    reference_key = f"{state_key}_reference_field"
    default_label = next(
        (
            label
            for preferred in ("display_name", "raw_label", "position")
            for key, label in reference_fields
            if key == preferred
        ),
        "Well position",
    )
    if st.session_state.get(reference_key) not in reference_keys_by_label:
        st.session_state[reference_key] = default_label
    reference_label = st.selectbox(
        "Show in 96-well layout",
        tuple(reference_keys_by_label),
        key=reference_key,
    )
    with st.expander("Reference plate", expanded=False):
        st.markdown(
            growth_selection_reference_html(
                growth_selection_reference(
                    wells,
                    field_key=reference_keys_by_label[reference_label],
                )
            ),
            unsafe_allow_html=True,
        )

    if not _is_streamlit_test():
        _sync_direct_selection_widgets(state_key, selected, all_positions)
    grid_tab, list_tab, filter_tab = st.tabs(
        ("96-well selection", "Selection list", "Metadata filters")
    )
    with grid_tab:
        st.caption(
            "Grid checks are staged locally and used only when Render selected curves is pressed."
        )
        with st.form(form_key):
            if _is_streamlit_test():
                st.caption("Interactive 8x12 selection grid enabled in the running app.")
                form_selected = selected
            else:
                edited_grid = st.data_editor(
                    growth_selection_grid(selected),
                    width="stretch",
                    column_config={
                        str(column): st.column_config.CheckboxColumn(str(column))
                        for column in range(1, 13)
                    },
                    key=_grid_editor_key(state_key),
                )
                form_selected = growth_selection_from_grid(edited_grid)
            form_result = render_form_controls(form_selected)
    if selection_submitted(form_result):
        selected = form_selected
        st.session_state[state_key] = selected
        st.session_state[_selection_list_key(state_key)] = selected
    with list_tab:
        if _is_streamlit_test():
            st.caption("Interactive selection list enabled in the running app.")
        else:
            by_position = _wells_by_position(wells)
            list_selected = tuple(
                st.multiselect(
                    "Selected wells (list)",
                    all_positions,
                    key=_selection_list_key(state_key),
                    format_func=lambda position: (
                        f"{position} · {_well_label(by_position[position], position)}"
                    ),
                    help="Changes immediately affect Render selected curves.",
                )
            )
            if not selection_submitted(form_result) and list_selected != selected:
                _store_selection(state_key, list_selected)
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

    return selected, form_result


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


def reference_plate_fields(
    wells: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, str], ...]:
    """Return meaningful layout fields that can label a reference plate."""

    by_position = _wells_by_position(wells)
    ordered_wells = tuple(by_position[position.label] for position in PLATE_96.positions())
    available = [
        (key, label)
        for key, label in _REFERENCE_FIELDS
        if key == "position"
        or any(_reference_text(_reference_value(well, key)) != "—" for well in ordered_wells)
    ]
    available.extend(
        (field.key, field.label)
        for field in growth_selection_fields(wells)
        if field.key.startswith("custom:")
    )
    return tuple(available)


def growth_selection_reference(
    wells: Sequence[Mapping[str, object]],
    *,
    field_key: str = "display_name",
) -> pd.DataFrame:
    """Build an 8x12 reference plate labeled by one layout field."""

    by_position = _wells_by_position(wells)
    available_keys = {key for key, _label in reference_plate_fields(wells)}
    if field_key not in available_keys:
        raise ValueError(f"Unknown or empty Growth reference field: {field_key}")
    return pd.DataFrame(
        [
            [
                _reference_text(
                    _reference_value(
                        by_position[f"{row}{column}"],
                        field_key,
                        position=f"{row}{column}",
                    )
                )
                for column in range(1, 13)
            ]
            for row in "ABCDEFGH"
        ],
        index=list("ABCDEFGH"),
        columns=[str(column) for column in range(1, 13)],
    )


def growth_selection_reference_html(frame: pd.DataFrame) -> str:
    """Render a wide reference plate inside its own horizontal scroll viewport."""

    table = frame.to_html(
        border=0,
        classes="growth-reference-plate-table",
        escape=True,
    )
    return (
        "<style>"
        ".growth-reference-plate-scroll{"
        "box-sizing:border-box;max-width:100%;width:100%;overflow-x:auto;"
        "overscroll-behavior-inline:contain;scrollbar-gutter:stable;}"
        ".growth-reference-plate-scroll .growth-reference-plate-table{"
        "border-collapse:collapse;min-width:90rem;width:max-content;}"
        ".growth-reference-plate-scroll .growth-reference-plate-table th,"
        ".growth-reference-plate-scroll .growth-reference-plate-table td{"
        "box-sizing:border-box;min-width:7rem;max-width:14rem;"
        "white-space:normal;overflow-wrap:anywhere;vertical-align:top;}"
        ".growth-reference-plate-scroll .growth-reference-plate-table thead th:first-child,"
        ".growth-reference-plate-scroll .growth-reference-plate-table tbody th{"
        "min-width:3rem;width:3rem;}"
        "</style>"
        '<div class="growth-reference-plate-scroll" '
        'aria-label="Scrollable reference plate">'
        f"{table}</div>"
    )


def _store_selection(state_key: str, positions: Iterable[str]) -> None:
    st.session_state[state_key] = tuple(positions)
    st.session_state[_direct_sync_key(state_key)] = True
    st.rerun()


def _sync_direct_selection_widgets(
    state_key: str,
    selected: Iterable[str],
    all_positions: Sequence[str],
) -> None:
    force_sync = bool(st.session_state.pop(_direct_sync_key(state_key), False))
    if force_sync:
        st.session_state.pop(_grid_editor_key(state_key), None)
    selected_set = set(selected)
    list_key = _selection_list_key(state_key)
    if force_sync or list_key not in st.session_state:
        st.session_state[list_key] = tuple(
            position for position in all_positions if position in selected_set
        )


def _grid_editor_key(state_key: str) -> str:
    return f"{state_key}_grid"


def _selection_list_key(state_key: str) -> str:
    return f"{state_key}_list"


def _direct_sync_key(state_key: str) -> str:
    return f"{state_key}_direct_sync_pending"


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


def _reference_value(
    well: Mapping[str, object],
    field_key: str,
    *,
    position: str = "",
) -> object:
    if field_key == "position":
        return position or well.get("position")
    if not field_key.startswith("custom:"):
        return well.get(field_key)
    custom = well.get("custom_json")
    if custom is None or custom == "":
        return None
    if isinstance(custom, Mapping):
        values = custom
    elif isinstance(custom, str):
        try:
            parsed = json.loads(custom)
        except json.JSONDecodeError as error:
            raise ValueError("Growth custom metadata must be valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("Growth custom metadata must be a JSON object")
        values = parsed
    else:
        raise ValueError("Growth custom metadata must be a JSON object")
    return values.get(field_key.removeprefix("custom:"))


def _reference_text(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if pd.isna(value):
        return "—"
    return str(value).strip() or "—"


def _boolean(value: object) -> bool:
    if pd.isna(value):
        return False
    return bool(value)


def _is_streamlit_test() -> bool:  # pragma: no cover - AppTest compatibility
    return os.environ.get("PLATE_READER_ENV", "").casefold() == "test"
