"""Shared 96-well grid/table editor used by both import wizards."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import MicWellLayoutChange, WellLayoutChange
from plate_reader.arrow_runtime import configure_arrow_memory_pool
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


def growth_layout_frame_from_wells(wells: Sequence[dict[str, object]]) -> pd.DataFrame:
    """Rehydrate the canonical editor frame from persisted repository rows."""

    by_position = {str(well["position"]): well for well in wells}
    custom_by_position = {
        position: _json_object(well.get("custom_json")) for position, well in by_position.items()
    }
    custom_names = sorted(
        {
            name
            for custom in custom_by_position.values()
            for name in custom
            if name != "t0_added_min"
        }
    )
    defaults = growth_layout_frame().set_index(WELL)
    rows: list[dict[str, object]] = []
    for position in PLATE_96.positions():
        well = by_position.get(position.label)
        if well is None:
            row = defaults.loc[position.label].to_dict()
            row[WELL] = position.label
        else:
            custom = custom_by_position[position.label]
            row = {
                WELL: position.label,
                "Raw label": well.get("raw_label") or "",
                "Display name": well.get("display_name") or "",
                "Blank": bool(well.get("is_blank")),
                "Background group": well.get("background_group") or "plate",
                "Plot": bool(well.get("plot_selected")),
                "Group": well.get("grouping_label") or "",
                "Media": well.get("medium") or "",
                "Strain": well.get("strain") or "",
                "Inoculum size": well.get("inoculum_size"),
                "Inoculum unit": well.get("inoculum_unit") or "",
                "Replicate": well.get("replicate") or 1,
                "Notes": well.get("notes") or "",
                "Treatment": well.get("treatment") or "",
                "Concentration": well.get("concentration"),
                "Concentration unit": well.get("concentration_unit") or "",
                "T0 added (min)": custom.get("t0_added_min"),
            }
            row.update({name: custom.get(name, "") for name in custom_names})
        rows.append(row)
    return pd.DataFrame(rows, columns=(*GROWTH_COLUMNS, *custom_names))


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


def mic_layout_frame_from_snapshot(
    wells: Sequence[dict[str, object]], raw_observations: Sequence[dict[str, object]]
) -> pd.DataFrame:
    """Rehydrate persisted MIC layout while keeping committed raw OD visible."""

    by_position = {str(well["position"]): well for well in wells}
    raw_by_well = {str(row["well_id"]): row["value_raw"] for row in raw_observations}
    custom_by_position = {
        position: _json_object(well.get("custom_json")) for position, well in by_position.items()
    }
    custom_names = sorted({name for custom in custom_by_position.values() for name in custom})
    rows: list[dict[str, object]] = []
    for position in PLATE_96.positions():
        well = by_position[position.label]
        custom = custom_by_position[position.label]
        row: dict[str, object] = {
            WELL: position.label,
            "Raw OD": raw_by_well[str(well["well_id"])],
            "Display name": well.get("display_name") or "",
            "Blank": bool(well.get("is_blank")),
            "Strain": well.get("strain") or "",
            "Antibiotic / treatment": well.get("treatment") or "",
            "Concentration": well.get("concentration"),
            "Concentration unit": well.get("concentration_unit") or "",
            "Media": well.get("medium") or "",
            "Replicate": well.get("replicate") or 1,
            "Notes": well.get("notes") or "",
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


def mic_layout_changes(
    frame: pd.DataFrame, *, include_raw: bool = True
) -> tuple[MicWellLayoutChange, ...]:
    normalized = normalize_layout_frame(frame)
    custom_columns = [column for column in normalized.columns if column not in MIC_COLUMNS]
    result = []
    for row in normalized.to_dict(orient="records"):
        result.append(
            MicWellLayoutChange(
                position=str(row[WELL]),
                value_raw=_optional_float(row["Raw OD"]) if include_raw else None,
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


def growth_template_layout(frame: pd.DataFrame) -> tuple[dict[str, object], ...]:
    """Serialize editable Growth fields without source-specific raw labels."""

    return tuple(asdict(change) for change in growth_layout_changes(frame))


def mic_template_layout(frame: pd.DataFrame) -> tuple[dict[str, object], ...]:
    """Serialize editable MIC fields without immutable raw OD measurements."""

    return tuple(
        {key: value for key, value in asdict(change).items() if key != "value_raw"}
        for change in mic_layout_changes(frame, include_raw=False)
    )


def apply_growth_template(
    frame: pd.DataFrame, layout: Sequence[Mapping[str, object]]
) -> pd.DataFrame:
    """Apply template metadata while preserving the current Growth raw labels."""

    current = normalize_layout_frame(frame).set_index(WELL)
    by_position = _template_rows(layout)
    custom_names = sorted(
        {
            name
            for row in by_position.values()
            for name in _mapping(row.get("custom_fields"))
            if name != "t0_added_min"
        }
    )
    rows: list[dict[str, object]] = []
    for position in PLATE_96.positions():
        saved = by_position[position.label]
        custom = _mapping(saved.get("custom_fields"))
        row: dict[str, object] = {
            WELL: position.label,
            "Raw label": current.at[position.label, "Raw label"],
            "Display name": saved.get("display_name") or "",
            "Blank": bool(saved.get("is_blank")),
            "Background group": saved.get("background_group") or "plate",
            "Plot": bool(saved.get("plot_selected")),
            "Group": saved.get("grouping_label") or "",
            "Media": saved.get("medium") or "",
            "Strain": saved.get("strain") or "",
            "Inoculum size": saved.get("inoculum_size"),
            "Inoculum unit": saved.get("inoculum_unit") or "",
            "Replicate": saved.get("replicate") or 1,
            "Notes": saved.get("notes") or "",
            "Treatment": saved.get("treatment") or "",
            "Concentration": saved.get("concentration"),
            "Concentration unit": saved.get("concentration_unit") or "",
            "T0 added (min)": custom.get("t0_added_min"),
        }
        row.update({name: custom.get(name, "") for name in custom_names})
        rows.append(row)
    return pd.DataFrame(rows, columns=(*GROWTH_COLUMNS, *custom_names))


def apply_mic_template(frame: pd.DataFrame, layout: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    """Apply template metadata while preserving the current MIC raw OD values."""

    current = normalize_layout_frame(frame).set_index(WELL)
    by_position = _template_rows(layout)
    custom_names = sorted(
        {name for row in by_position.values() for name in _mapping(row.get("custom_labels"))}
    )
    rows: list[dict[str, object]] = []
    for position in PLATE_96.positions():
        saved = by_position[position.label]
        custom = _mapping(saved.get("custom_labels"))
        row: dict[str, object] = {
            WELL: position.label,
            "Raw OD": current.at[position.label, "Raw OD"],
            "Display name": saved.get("display_name") or "",
            "Blank": bool(saved.get("is_blank")),
            "Strain": saved.get("strain") or "",
            "Antibiotic / treatment": saved.get("treatment") or "",
            "Concentration": saved.get("concentration"),
            "Concentration unit": saved.get("concentration_unit") or "",
            "Media": saved.get("medium") or "",
            "Replicate": saved.get("replicate") or 1,
            "Notes": saved.get("notes") or "",
        }
        row.update({name: custom.get(name, "") for name in custom_names})
        rows.append(row)
    return pd.DataFrame(rows, columns=(*MIC_COLUMNS, *custom_names))


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


def include_layout_columns(frame: pd.DataFrame, column_names: Sequence[str]) -> pd.DataFrame:
    """Add missing universal columns without changing existing plate values."""

    updated = normalize_layout_frame(frame)
    known = {str(column).casefold() for column in updated.columns}
    for raw_name in column_names:
        name = str(raw_name).strip()
        if name and name.casefold() not in known:
            updated[name] = ""
            known.add(name.casefold())
    return updated


def replace_editor_frame(state_key: str, frame: pd.DataFrame) -> None:
    """Replace a staged editor frame and invalidate both synchronized widgets."""

    revision_key = f"{state_key}_revision"
    st.session_state[state_key] = normalize_layout_frame(frame)
    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1


def render_plate_editor(
    initial_frame: pd.DataFrame,
    *,
    state_key: str,
    assay: str,
    immutable_columns: Sequence[str] = (),
    suggestions: Mapping[str, Sequence[str]] | None = None,
    universal_custom_columns: Sequence[str] = (),
    add_custom_column: Callable[[str], None] | None = None,
    delete_custom_column: Callable[[str], None] | None = None,
) -> pd.DataFrame:  # pragma: no cover - Streamlit widget composition
    """Render the legacy dual-view editor and return its canonical session frame."""

    configure_arrow_memory_pool()
    revision_key = f"{state_key}_revision"
    if state_key not in st.session_state:
        st.session_state[state_key] = include_layout_columns(
            initial_frame, universal_custom_columns
        )
        st.session_state[revision_key] = 0
    frame = include_layout_columns(st.session_state[state_key], universal_custom_columns)
    st.session_state[state_key] = frame
    revision = int(st.session_state[revision_key])

    st.caption(
        "The 96-well plate and full table edit the same staged layout. Apply changes in either "
        "view to synchronize the other view. Nothing reaches the database until the page's "
        "final save or commit action."
    )
    if _is_streamlit_test():
        plate_tab, table_tab = st.tabs(("96-well plate", "Full well table"))
        with plate_tab:
            st.caption("Interactive 8x12 plate editor enabled in the running app.")
        with table_tab:
            st.caption("Interactive 96-row table editor enabled in the running app.")
        return frame
    _render_custom_columns(
        frame,
        state_key,
        revision_key,
        assay,
        universal_custom_columns,
        add_custom_column,
        delete_custom_column,
    )
    frame = normalize_layout_frame(st.session_state[state_key])
    _render_fill_helpers(
        frame,
        state_key,
        revision_key,
        assay,
        immutable_columns,
        suggestions or {},
    )
    frame = normalize_layout_frame(st.session_state[state_key])

    plate_tab, table_tab = st.tabs(("96-well plate", "Full well table"))
    with plate_tab:
        protected = {*PROTECTED_COLUMNS, *immutable_columns}
        editable = [column for column in frame.columns if column not in protected]
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
                _editor_safe_frame(frame),
                height=600,
                width="stretch",
                hide_index=True,
                disabled=[
                    WELL,
                    *(["Raw label"] if "Raw label" in frame else []),
                    *immutable_columns,
                ],
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
    frame: pd.DataFrame,
    state_key: str,
    revision_key: str,
    assay: str,
    universal_custom_columns: Sequence[str],
    add_custom_column: Callable[[str], None] | None,
    delete_custom_column: Callable[[str], None] | None,
) -> None:
    with st.expander("Manage custom columns"):
        export_note = " and included in Growth tabular exports" if assay == "growth" else ""
        st.caption(
            f"New columns are shared with every {assay.upper()} layout{export_note}. "
            "Values remain specific to each experiment."
        )
        add_name, add_button = st.columns((3, 1))
        new_name = add_name.text_input("New custom column", key=f"{state_key}_new_column")
        if add_button.button("Add column", key=f"{state_key}_add_column"):
            clean_name = new_name.strip()
            if not clean_name:
                st.error("Enter a custom column name.")
            elif clean_name.casefold() in {str(column).casefold() for column in frame.columns}:
                st.error(f"{clean_name} already exists.")
            else:
                try:
                    if add_custom_column is not None:
                        add_custom_column(clean_name)
                except Exception as error:
                    st.error(f"Could not add universal custom column: {error}")
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
                universal = {
                    str(column).casefold(): str(column) for column in universal_custom_columns
                }
                try:
                    if selected.casefold() in universal and delete_custom_column is not None:
                        delete_custom_column(universal[selected.casefold()])
                except Exception as error:
                    st.error(f"Could not remove universal custom column: {error}")
                else:
                    _replace_frame(state_key, revision_key, frame.drop(columns=selected))


def _render_fill_helpers(
    frame: pd.DataFrame,
    state_key: str,
    revision_key: str,
    assay: str,
    immutable_columns: Sequence[str],
    suggestions: Mapping[str, Sequence[str]],
) -> None:  # pragma: no cover - Streamlit widget composition
    with st.expander("Fill helpers", expanded=True):
        protected = {*PROTECTED_COLUMNS, *immutable_columns}
        editable = [column for column in frame.columns if column not in protected]
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
        value = _fill_value_widget(target_column, state_key, assay, suggestions)
        if st.button("Apply fill", key=f"{state_key}_fill_apply"):
            _replace_frame(
                state_key,
                revision_key,
                fill_layout(frame, target_column, value, scope, target),
            )


def _fill_value_widget(  # pragma: no cover - Streamlit widget composition
    column: str,
    state_key: str,
    assay: str,
    suggestions: Mapping[str, Sequence[str]],
) -> object:
    key = f"{state_key}_fill_value_{column}"
    if column in BOOLEAN_COLUMNS:
        return st.checkbox("Fill value", key=key)
    if column == "Replicate":
        return st.number_input("Fill value", min_value=1, value=1, step=1, key=key)
    if column in NUMERIC_COLUMNS:
        return st.number_input("Fill value", value=0.0, key=key)
    default = "LB" if column == "Media" and assay == "growth" else ""
    saved = tuple(dict.fromkeys(suggestions.get(column, ())))
    if saved:
        options = tuple(dict.fromkeys(((default,) if default else ()) + saved))
        return st.selectbox(
            "Fill value",
            options,
            index=0 if default else None,
            accept_new_options=True,
            placeholder="Choose a saved value or type a new one",
            key=key,
        )
    return st.text_input("Fill value", value=default, key=key)


def _replace_frame(  # pragma: no cover - Streamlit widget composition
    state_key: str, revision_key: str, frame: pd.DataFrame
) -> None:
    replace_editor_frame(state_key, frame)
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


def _editor_safe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Give Arrow-backed widgets one stable dtype per column."""

    safe = frame.copy(deep=True)
    for column in safe.columns:
        if column in BOOLEAN_COLUMNS:
            safe[column] = safe[column].astype("boolean")
        elif column == "Replicate":
            safe[column] = pd.to_numeric(safe[column], errors="coerce").astype("Int64")
        elif column in NUMERIC_COLUMNS:
            safe[column] = pd.to_numeric(safe[column], errors="coerce").astype("Float64")
        else:
            safe[column] = safe[column].fillna("").astype("string")
    return safe


def _custom_values(row: Mapping[str, object], columns: Sequence[str]) -> dict[str, object]:
    return {
        column: value
        for column in columns
        if (value := row[column]) is not None and not _is_missing(value) and str(value) != ""
    }


def _template_rows(
    layout: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    expected = {position.label for position in PLATE_96.positions()}
    by_position = {str(row.get("position", "")): row for row in layout}
    if len(layout) != 96 or set(by_position) != expected:
        raise ValueError("A plate template must contain each A1-H12 position exactly once")
    return by_position


def _mapping(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Template custom fields must be a JSON object")
    return value


def _json_object(value: object) -> dict[str, object]:
    if value is None or value == "":
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Well custom metadata must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


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
    """Keep broad workflow smoke tests focused; dedicated tests exercise both editors."""

    return os.environ.get("PLATE_READER_ENV", "").casefold() == "test"
