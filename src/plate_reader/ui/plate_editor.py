"""Shared 96-well grid/table editor used by both import wizards."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import MicWellLayoutChange, WellLayoutChange
from plate_reader.domain.common.plate import PLATE_96
from plate_reader.domain.mic import MicWell

WELL = "Well"

GROWTH_COLUMNS = (
    WELL,
    "Raw label",
    "Display name",
    "Blank",
    "Background group",
    "Plot",
    "Group",
    "Media",
    "Strain",
    "Inoculum size",
    "Inoculum unit",
    "Replicate",
    "Notes",
    "Treatment",
    "Concentration",
    "Concentration unit",
    "T0 added (min)",
)

MIC_COLUMNS = (
    WELL,
    "Raw OD",
    "Display name",
    "Blank",
    "Strain",
    "Antibiotic / treatment",
    "Concentration",
    "Concentration unit",
    "Media",
    "Replicate",
    "Notes",
)

BOOLEAN_COLUMNS = {"Blank", "Plot"}
NUMERIC_COLUMNS = {"Raw OD", "Inoculum size", "Replicate", "Concentration", "T0 added (min)"}
PROTECTED_COLUMNS = {WELL, "Raw label"}


def growth_layout_frame(labels: Mapping[str, str] | None = None) -> pd.DataFrame:
    label_by_well = labels or {}
    rows: list[dict[str, object]] = []
    for position in PLATE_96.positions():
        raw_label = label_by_well.get(position.label, "")
        rows.append(
            {
                WELL: position.label,
                "Raw label": raw_label,
                "Display name": raw_label,
                "Blank": False,
                "Background group": "plate",
                "Plot": False,
                "Group": "",
                "Media": "LB",
                "Strain": "",
                "Inoculum size": None,
                "Inoculum unit": "OD600",
                "Replicate": 1,
                "Notes": "",
                "Treatment": "",
                "Concentration": None,
                "Concentration unit": "",
                "T0 added (min)": None,
            }
        )
    return pd.DataFrame(rows, columns=GROWTH_COLUMNS)


def mic_layout_frame(wells: Sequence[MicWell]) -> pd.DataFrame:
    by_position = {well.position.label: well for well in wells}
    rows: list[dict[str, object]] = []
    custom_names = sorted({name for well in wells for name, _value in well.custom_labels})
    for position in PLATE_96.positions():
        well = by_position[position.label]
        custom = dict(well.custom_labels)
        row: dict[str, object] = {
            WELL: position.label,
            "Raw OD": well.value_raw,
            "Display name": "",
            "Blank": well.is_blank,
            "Strain": well.strain or "",
            "Antibiotic / treatment": well.treatment or "",
            "Concentration": well.concentration,
            "Concentration unit": well.concentration_unit,
            "Media": well.medium or "",
            "Replicate": well.replicate,
            "Notes": well.notes or "",
        }
        row.update({name: custom.get(name, "") for name in custom_names})
        rows.append(row)
    return pd.DataFrame(rows, columns=(*MIC_COLUMNS, *custom_names))


def plate_matrix(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    ordered = normalize_layout_frame(frame)
    values = ordered[column].tolist()
    return pd.DataFrame(
        [values[offset : offset + 12] for offset in range(0, 96, 12)],
        index=list("ABCDEFGH"),
        columns=[str(number) for number in range(1, 13)],
    )


def apply_plate_matrix(frame: pd.DataFrame, column: str, matrix: pd.DataFrame) -> pd.DataFrame:
    if matrix.shape != (8, 12):
        raise ValueError("A plate grid must contain exactly 8 rows and 12 columns")
    updated = normalize_layout_frame(frame)
    updated[column] = matrix.to_numpy().reshape(96).tolist()
    return updated


def fill_layout(
    frame: pd.DataFrame,
    column: str,
    value: object,
    scope: str,
    target: str | int | None = None,
) -> pd.DataFrame:
    updated = normalize_layout_frame(frame)
    if scope == "Full plate":
        mask = pd.Series(True, index=updated.index)
    elif scope == "Row":
        if not isinstance(target, str) or target not in "ABCDEFGH":
            raise ValueError("Choose a plate row from A through H")
        mask = updated[WELL].str.startswith(target)
    elif scope == "Column":
        if not isinstance(target, int) or not 1 <= target <= 12:
            raise ValueError("Choose a plate column from 1 through 12")
        mask = updated[WELL].str[1:].astype(int).eq(target)
    else:
        raise ValueError(f"Unsupported fill scope: {scope}")
    updated.loc[mask, column] = value
    return updated


def growth_layout_changes(frame: pd.DataFrame) -> tuple[WellLayoutChange, ...]:
    normalized = normalize_layout_frame(frame)
    custom_columns = [column for column in normalized.columns if column not in GROWTH_COLUMNS]
    result = []
    for row in normalized.to_dict(orient="records"):
        custom_fields = _custom_values(row, custom_columns)
        t0 = _optional_float(row["T0 added (min)"])
        if t0 is not None:
            custom_fields["t0_added_min"] = t0
        result.append(
            WellLayoutChange(
                position=str(row[WELL]),
                display_name=_optional_text(row["Display name"]),
                is_blank=_boolean(row["Blank"]),
                background_group=_optional_text(row["Background group"]) or "plate",
                strain=_optional_text(row["Strain"]),
                medium=_optional_text(row["Media"]),
                treatment=_optional_text(row["Treatment"]),
                concentration=_optional_float(row["Concentration"]),
                concentration_unit=_optional_text(row["Concentration unit"]),
                replicate=_positive_int(row["Replicate"]),
                plot_selected=_boolean(row["Plot"]),
                notes=_optional_text(row["Notes"]),
                grouping_label=_optional_text(row["Group"]),
                inoculum_size=_optional_float(row["Inoculum size"]),
                inoculum_unit=_optional_text(row["Inoculum unit"]),
                custom_fields=custom_fields,
            )
        )
    return tuple(result)


def mic_layout_changes(frame: pd.DataFrame) -> tuple[MicWellLayoutChange, ...]:
    normalized = normalize_layout_frame(frame)
    custom_columns = [column for column in normalized.columns if column not in MIC_COLUMNS]
    result = []
    for row in normalized.to_dict(orient="records"):
        result.append(
            MicWellLayoutChange(
                position=str(row[WELL]),
                value_raw=_optional_float(row["Raw OD"]),
                display_name=_optional_text(row["Display name"]),
                is_blank=_boolean(row["Blank"]),
                strain=_optional_text(row["Strain"]),
                treatment=_optional_text(row["Antibiotic / treatment"]),
                concentration=_optional_float(row["Concentration"]),
                concentration_unit=_optional_text(row["Concentration unit"]),
                medium=_optional_text(row["Media"]),
                replicate=_positive_int(row["Replicate"]),
                notes=_optional_text(row["Notes"]),
                custom_labels={
                    key: str(value) for key, value in _custom_values(row, custom_columns).items()
                },
            )
        )
    return tuple(result)


def normalize_layout_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if WELL not in frame:
        raise ValueError("The layout table is missing its Well column")
    expected = [position.label for position in PLATE_96.positions()]
    if len(frame) != 96 or set(frame[WELL].astype(str)) != set(expected):
        raise ValueError("The layout must contain each A1-H12 well exactly once")
    normalized = frame.copy(deep=True)
    normalized[WELL] = normalized[WELL].astype(str)
    order = {well: index for index, well in enumerate(expected)}
    normalized["__order"] = normalized[WELL].map(order)
    return normalized.sort_values("__order").drop(columns="__order").reset_index(drop=True)


def render_plate_editor(
    initial_frame: pd.DataFrame,
    *,
    state_key: str,
    assay: str,
) -> pd.DataFrame:  # pragma: no cover - Streamlit widget composition
    """Render the legacy dual-view editor and return its canonical session frame."""

    revision_key = f"{state_key}_revision"
    if state_key not in st.session_state:
        st.session_state[state_key] = normalize_layout_frame(initial_frame)
        st.session_state[revision_key] = 0
    frame = normalize_layout_frame(st.session_state[state_key])
    revision = int(st.session_state[revision_key])

    st.caption(
        "The 96-well plate and full table edit the same staged layout. Apply changes in either "
        "view to synchronize the other view. Nothing reaches the database until Step 5."
    )
    if _is_streamlit_test():
        plate_tab, table_tab = st.tabs(("96-well plate", "Full well table"))
        with plate_tab:
            st.caption("Interactive 8x12 plate editor enabled in the running app.")
        with table_tab:
            st.caption("Interactive 96-row table editor enabled in the running app.")
        return frame
    _render_custom_columns(frame, state_key, revision_key)
    frame = normalize_layout_frame(st.session_state[state_key])
    _render_fill_helpers(frame, state_key, revision_key, assay)
    frame = normalize_layout_frame(st.session_state[state_key])

    plate_tab, table_tab = st.tabs(("96-well plate", "Full well table"))
    with plate_tab:
        editable = [column for column in frame.columns if column not in PROTECTED_COLUMNS]
        parameter = st.selectbox(
            "Plate parameter",
            editable,
            index=editable.index("Strain") if "Strain" in editable else 0,
            key=f"{state_key}_parameter",
        )
        with st.form(f"{state_key}_plate_form_{parameter}_{revision}"):
            edited_grid = st.data_editor(
                plate_matrix(frame, parameter),
                width="stretch",
                key=f"{state_key}_plate_{parameter}_{revision}",
            )
            apply_grid = st.form_submit_button("Apply 96-well plate changes", type="primary")
        if apply_grid:
            _replace_frame(
                state_key,
                revision_key,
                apply_plate_matrix(frame, parameter, edited_grid),
            )

    with table_tab:
        with st.form(f"{state_key}_table_form_{revision}"):
            edited_table = st.data_editor(
                frame,
                height=600,
                width="stretch",
                hide_index=True,
                disabled=[WELL, *(["Raw label"] if "Raw label" in frame else [])],
                column_config=_column_config(frame),
                key=f"{state_key}_table_{revision}",
            )
            apply_table = st.form_submit_button("Apply full table changes", type="primary")
        if apply_table:
            _replace_frame(state_key, revision_key, normalize_layout_frame(edited_table))

    if assay == "growth":
        with st.expander("Paste selection for plotting"):
            pasted = st.text_area(
                "Paste raw or display names (one per line)", key=f"{state_key}_paste"
            )
            if st.button("Select matching wells for plotting", key=f"{state_key}_paste_apply"):
                names = {line.strip() for line in pasted.splitlines() if line.strip()}
                updated = frame.copy(deep=True)
                match = updated["Raw label"].isin(names) | updated["Display name"].isin(names)
                updated.loc[match, "Plot"] = True
                st.success(f"Selected {int(match.sum())} matching well(s).")
                _replace_frame(state_key, revision_key, updated)

    return normalize_layout_frame(st.session_state[state_key])


def _render_custom_columns(  # pragma: no cover - Streamlit widget composition
    frame: pd.DataFrame, state_key: str, revision_key: str
) -> None:
    with st.expander("Manage custom columns"):
        add_name, add_button = st.columns((3, 1))
        new_name = add_name.text_input("New custom column", key=f"{state_key}_new_column")
        if add_button.button("Add column", key=f"{state_key}_add_column"):
            clean_name = new_name.strip()
            if not clean_name:
                st.error("Enter a custom column name.")
            elif clean_name in frame.columns:
                st.error(f"{clean_name} already exists.")
            else:
                updated = frame.copy(deep=True)
                updated[clean_name] = ""
                _replace_frame(state_key, revision_key, updated)
        removable = [
            column for column in frame.columns if column not in (*GROWTH_COLUMNS, *MIC_COLUMNS)
        ]
        if removable:
            remove_name, remove_button = st.columns((3, 1))
            selected = remove_name.selectbox(
                "Remove custom column", removable, key=f"{state_key}_remove_column"
            )
            if remove_button.button("Remove column", key=f"{state_key}_remove_column_button"):
                _replace_frame(state_key, revision_key, frame.drop(columns=selected))


def _render_fill_helpers(
    frame: pd.DataFrame, state_key: str, revision_key: str, assay: str
) -> None:  # pragma: no cover - Streamlit widget composition
    with st.expander("Fill helpers", expanded=True):
        editable = [column for column in frame.columns if column not in PROTECTED_COLUMNS]
        target_column = st.selectbox("Fill parameter", editable, key=f"{state_key}_fill_parameter")
        scope = st.radio(
            "Fill area",
            ("Full plate", "Row", "Column"),
            horizontal=True,
            key=f"{state_key}_fill_scope",
        )
        target: str | int | None = None
        if scope == "Row":
            target = st.selectbox("Target row", tuple("ABCDEFGH"), key=f"{state_key}_fill_row")
        elif scope == "Column":
            target = st.selectbox(
                "Target column", tuple(range(1, 13)), key=f"{state_key}_fill_column"
            )
        value = _fill_value_widget(target_column, state_key, assay)
        if st.button("Apply fill", key=f"{state_key}_fill_apply"):
            _replace_frame(
                state_key,
                revision_key,
                fill_layout(frame, target_column, value, scope, target),
            )


def _fill_value_widget(  # pragma: no cover - Streamlit widget composition
    column: str, state_key: str, assay: str
) -> object:
    key = f"{state_key}_fill_value_{column}"
    if column in BOOLEAN_COLUMNS:
        return st.checkbox("Fill value", key=key)
    if column == "Replicate":
        return st.number_input("Fill value", min_value=1, value=1, step=1, key=key)
    if column in NUMERIC_COLUMNS:
        return st.number_input("Fill value", value=0.0, key=key)
    default = "LB" if column == "Media" and assay == "growth" else ""
    return st.text_input("Fill value", value=default, key=key)


def _replace_frame(  # pragma: no cover - Streamlit widget composition
    state_key: str, revision_key: str, frame: pd.DataFrame
) -> None:
    st.session_state[state_key] = normalize_layout_frame(frame)
    st.session_state[revision_key] = int(st.session_state[revision_key]) + 1
    st.rerun()


def _column_config(  # pragma: no cover - Streamlit widget composition
    frame: pd.DataFrame,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        WELL: st.column_config.TextColumn("Well", disabled=True),
        "Blank": st.column_config.CheckboxColumn("Blank"),
        "Plot": st.column_config.CheckboxColumn("Plot"),
        "Replicate": st.column_config.NumberColumn("Replicate", min_value=1, step=1),
        "Raw OD": st.column_config.NumberColumn("Raw OD", format="%.4f"),
        "Inoculum size": st.column_config.NumberColumn("Inoculum size", format="%.4f"),
        "Concentration": st.column_config.NumberColumn("Concentration", format="%.4g"),
        "T0 added (min)": st.column_config.NumberColumn("T0 added (min)", format="%.2f"),
    }
    return {key: value for key, value in config.items() if key in frame}


def _custom_values(row: Mapping[str, object], columns: Sequence[str]) -> dict[str, object]:
    return {
        column: value
        for column in columns
        if (value := row[column]) is not None and not _is_missing(value) and str(value) != ""
    }


def _optional_text(value: object) -> str | None:
    if value is None or _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None or _is_missing(value) or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("A numeric well field cannot contain true/false")
    return float(str(value))


def _positive_int(value: object) -> int:
    number = _optional_float(value)
    if number is None or not number.is_integer() or number < 1:
        raise ValueError("Every replicate must be a positive whole number")
    return int(number)


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or _is_missing(value):
        return False
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1", "blank"}:
            return True
        if normalized in {"false", "no", "0", ""}:
            return False
    return bool(value)


def _is_missing(value: object) -> bool:
    missing = pd.isna(value)
    return bool(missing) if isinstance(missing, bool) else False


def _is_streamlit_test() -> bool:  # pragma: no cover - AppTest compatibility
    """Avoid a PyArrow crash in Streamlit's in-process AppTest runner."""

    return os.environ.get("PLATE_READER_ENV", "").casefold() == "test"
