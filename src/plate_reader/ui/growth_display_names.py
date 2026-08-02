"""Growth display-name builder and layout CSV controls."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from plate_reader.application.services import (
    BuildGrowthDisplayNamesService,
    GrowthDisplayNameAction,
    GrowthDisplayNameOptions,
    GrowthDisplayNamePreview,
    GrowthDisplayNameScope,
    GrowthDisplayNameToken,
    GrowthDisplayNumberFormat,
    export_growth_display_name_csv,
    preview_growth_display_name_csv,
)
from plate_reader.domain.common.plate import PLATE_96
from plate_reader.ui.plate_editor import (
    GROWTH_COLUMNS,
    normalize_layout_frame,
    replace_editor_frame,
)

_WELL_FIELDS = (
    ("position", "Well"),
    ("raw_label", "Raw label"),
    ("strain", "Strain"),
    ("treatment", "Treatment"),
    ("concentration", "Concentration"),
    ("concentration_unit", "Concentration unit"),
    ("medium", "Media"),
    ("grouping_label", "Group"),
    ("inoculum_size", "Inoculum size"),
    ("inoculum_unit", "Inoculum unit"),
    ("replicate", "Replicate"),
    ("t0_added_min", "T0 added (min)"),
)
_PLATE_FIELDS = (
    ("experiment_name", "Experiment name"),
    ("plate_name", "Plate name"),
    ("experiment_date", "Date"),
    ("project", "Project"),
    ("instrument", "Instrument"),
    ("temperature", "Temperature"),
    ("temperature_unit", "Temperature unit"),
    ("channel", "Channel"),
    ("tags", "Tags"),
)
_NUMBER_FORMAT_LABELS = {
    GrowthDisplayNumberFormat.GENERAL: "General (up to 6 significant digits)",
    GrowthDisplayNumberFormat.TWO_DECIMALS: "2 decimal places",
    GrowthDisplayNumberFormat.THREE_DECIMALS: "3 decimal places",
    GrowthDisplayNumberFormat.FOUR_DECIMALS: "4 decimal places",
}


@dataclass(frozen=True, slots=True)
class _TokenChoice:
    label: str
    token: GrowthDisplayNameToken


def render_growth_display_name_controls(
    frame: pd.DataFrame,
    plate_metadata: Mapping[str, object],
    *,
    state_key: str,
    selected_positions: Iterable[str] = (),
) -> None:  # pragma: no cover - Streamlit widget composition
    """Stage generated/imported names in the existing shared Layout editor."""

    message_key = f"{state_key}_display_name_message"
    if message := st.session_state.pop(message_key, None):
        st.success(str(message))
    revision = int(st.session_state.get(f"{state_key}_revision", 0))
    normalized = normalize_layout_frame(frame)
    wells = growth_display_name_wells(normalized)
    metadata = growth_display_name_metadata(plate_metadata)
    selected = tuple(selected_positions)

    with st.expander("Display name builder", expanded=False):
        st.caption(
            "Preview names before applying them to the staged Layout. Nothing is written to "
            "the database until Save full layout (or the final import commit) is pressed."
        )
        formula_tab, csv_tab = st.tabs(("Build from metadata", "CSV template"))
        with formula_tab:
            _render_formula_builder(
                normalized,
                wells,
                metadata,
                selected,
                state_key,
                revision,
                message_key,
            )
        with csv_tab:
            _render_csv_builder(
                normalized,
                wells,
                state_key,
                revision,
                message_key,
            )


def growth_display_name_wells(frame: pd.DataFrame) -> tuple[dict[str, object], ...]:
    """Translate the shared editor frame into UI-independent builder records."""

    normalized = normalize_layout_frame(frame)
    custom_columns = [column for column in normalized.columns if column not in GROWTH_COLUMNS]
    records = []
    for row in normalized.to_dict(orient="records"):
        custom = {
            column: _clean_value(row.get(column))
            for column in custom_columns
            if _clean_value(row.get(column)) is not None
        }
        records.append(
            {
                "position": str(row["Well"]),
                "display_name": str(row.get("Display name") or ""),
                "raw_label": _clean_value(row.get("Raw label")),
                "strain": _clean_value(row.get("Strain")),
                "treatment": _clean_value(row.get("Treatment")),
                "concentration": _clean_value(row.get("Concentration")),
                "concentration_unit": _clean_value(row.get("Concentration unit")),
                "medium": _clean_value(row.get("Media")),
                "grouping_label": _clean_value(row.get("Group")),
                "inoculum_size": _clean_value(row.get("Inoculum size")),
                "inoculum_unit": _clean_value(row.get("Inoculum unit")),
                "replicate": _clean_value(row.get("Replicate")),
                "t0_added_min": _clean_value(row.get("T0 added (min)")),
                "custom_fields": custom,
            }
        )
    return tuple(records)


def growth_display_name_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "experiment_name": metadata.get("name") or metadata.get("experiment_name"),
        "plate_name": metadata.get("plate_name"),
        "experiment_date": metadata.get("experiment_date"),
        "project": metadata.get("project"),
        "instrument": metadata.get("instrument"),
        "temperature": metadata.get("temperature"),
        "temperature_unit": metadata.get("temperature_unit"),
        "channel": metadata.get("channel"),
        "tags": metadata.get("tags") or (),
    }


def apply_growth_display_name_preview(
    frame: pd.DataFrame, preview: GrowthDisplayNamePreview
) -> pd.DataFrame:
    updated = normalize_layout_frame(frame)
    row_by_position = {str(position): index for index, position in enumerate(updated["Well"])}
    for change in preview.changes:
        if change.position not in row_by_position:
            raise ValueError(f"Display-name preview contains unknown well: {change.position}")
        updated.at[row_by_position[change.position], "Display name"] = change.proposed_name
    return updated


def _render_formula_builder(
    frame: pd.DataFrame,
    wells: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object],
    selected_positions: tuple[str, ...],
    state_key: str,
    revision: int,
    message_key: str,
) -> None:
    choices = _token_choices(frame)
    by_label = {choice.label: choice.token for choice in choices}
    default_labels = tuple(
        label
        for label in (
            "Well · Strain",
            "Well · Treatment",
            "Well · Concentration",
            "Well · Concentration unit",
            "Well · Replicate",
        )
        if label in by_label
    )
    selected_labels = tuple(
        st.multiselect(
            "Fields in display-name order",
            tuple(by_label),
            default=default_labels,
            key=f"{state_key}_display_name_fields",
        )
    )
    st.caption("The selected chips are concatenated from left to right; remove/re-add to reorder.")
    formatting = st.columns(4)
    separator = formatting[0].text_input(
        "Separator", value="_", key=f"{state_key}_display_name_separator"
    )
    prefix = formatting[1].text_input("Prefix", value="", key=f"{state_key}_display_name_prefix")
    suffix = formatting[2].text_input("Suffix", value="", key=f"{state_key}_display_name_suffix")
    number_format = formatting[3].selectbox(
        "Number format",
        tuple(GrowthDisplayNumberFormat),
        format_func=lambda value: _NUMBER_FORMAT_LABELS[value],
        key=f"{state_key}_display_name_number_format",
    )
    omit_empty = st.checkbox(
        "Omit empty fields",
        value=True,
        key=f"{state_key}_display_name_omit_empty",
    )
    target = st.selectbox(
        "Apply formula to",
        ("Full plate", "Selected wells"),
        key=f"{state_key}_display_name_target",
    )
    targets = (
        tuple(position.label for position in PLATE_96.positions())
        if target == "Full plate"
        else selected_positions
    )
    if target == "Selected wells":
        st.caption(
            f"Using {len(targets)} staged well(s) from the Plotting selector or saved Plot flags."
        )
    tokens = tuple(by_label[label] for label in selected_labels)
    options = (
        GrowthDisplayNameOptions(
            tokens=tokens,
            separator=separator,
            prefix=prefix,
            suffix=suffix,
            omit_empty=omit_empty,
            number_format=number_format,
        )
        if tokens
        else None
    )
    signature = (
        revision,
        selected_labels,
        separator,
        prefix,
        suffix,
        omit_empty,
        str(number_format),
        target,
        targets,
    )
    preview_key = f"{state_key}_display_name_formula_preview"
    if st.button(
        "Preview generated names",
        type="primary",
        disabled=options is None or not targets,
        key=f"{state_key}_display_name_preview_button",
    ):
        try:
            assert options is not None
            preview = BuildGrowthDisplayNamesService().execute(
                wells,
                metadata,
                targets,
                options,
            )
            st.session_state[preview_key] = (signature, preview)
        except Exception as error:
            st.error(str(error))
    stored = st.session_state.get(preview_key)
    if isinstance(stored, tuple) and len(stored) == 2 and stored[0] == signature:
        _render_preview_and_apply(
            frame,
            stored[1],
            state_key=state_key,
            preview_key=preview_key,
            message_key=message_key,
            source="generated",
            signature=signature,
        )


def _render_csv_builder(
    frame: pd.DataFrame,
    wells: Sequence[Mapping[str, object]],
    state_key: str,
    revision: int,
    message_key: str,
) -> None:
    st.download_button(
        "Download display-name CSV template",
        data=export_growth_display_name_csv(wells),
        file_name="growth-display-names.csv",
        mime="text/csv",
        key=f"{state_key}_display_name_csv_download",
    )
    upload = st.file_uploader(
        "Display-name CSV",
        type=("csv",),
        key=f"{state_key}_display_name_csv_upload",
    )
    content = upload.getvalue() if upload is not None else None
    content_hash = hashlib.sha256(content).hexdigest() if content is not None else ""
    signature = (revision, content_hash)
    preview_key = f"{state_key}_display_name_csv_preview"
    if st.button(
        "Preview uploaded names",
        type="primary",
        disabled=content is None,
        key=f"{state_key}_display_name_csv_preview_button",
    ):
        try:
            assert content is not None
            preview = preview_growth_display_name_csv(wells, content)
            st.session_state[preview_key] = (signature, preview)
        except Exception as error:
            st.error(str(error))
    stored = st.session_state.get(preview_key)
    if isinstance(stored, tuple) and len(stored) == 2 and stored[0] == signature:
        _render_preview_and_apply(
            frame,
            stored[1],
            state_key=state_key,
            preview_key=preview_key,
            message_key=message_key,
            source="uploaded",
            signature=signature,
        )


def _render_preview_and_apply(
    frame: pd.DataFrame,
    preview: object,
    *,
    state_key: str,
    preview_key: str,
    message_key: str,
    source: str,
    signature: object,
) -> None:
    if not isinstance(preview, GrowthDisplayNamePreview):
        st.error("Display-name preview state is invalid; preview again.")
        return
    _render_preview(preview)
    confirmation_required = preview.overwrite_count > 0
    confirmation = not confirmation_required or st.checkbox(
        (
            f"Confirm replacing {preview.overwrite_count} existing name(s), including "
            f"{preview.clear_count} clear(s)"
        ),
        key=f"{state_key}_{source}_display_name_confirm_{_signature_key(signature)}",
    )
    if st.button(
        f"Apply {source} names to staged layout",
        type="primary",
        disabled=preview.changed_count == 0 or not confirmation,
        key=f"{state_key}_{source}_display_name_apply_{_signature_key(signature)}",
    ):
        replace_editor_frame(state_key, apply_growth_display_name_preview(frame, preview))
        st.session_state.pop(preview_key, None)
        st.session_state[message_key] = (
            f"Applied {preview.changed_count} display-name change(s) to the staged layout."
        )
        st.rerun()


def _render_preview(preview: GrowthDisplayNamePreview) -> None:
    st.caption(
        f"{preview.changed_count} change(s); {preview.overwrite_count} replacement(s); "
        f"{preview.clear_count} clear(s)."
    )
    rows = [
        {
            "Well": change.position,
            "Current": change.previous_name,
            "Proposed": change.proposed_name,
            "Action": change.action.value,
        }
        for change in preview.changes
        if change.action is not GrowthDisplayNameAction.UNCHANGED
    ]
    if not rows:
        st.info("The preview matches the current staged display names.")
        return
    html = pd.DataFrame.from_records(rows).to_html(index=False, escape=True)
    st.markdown(
        f'<div style="max-height: 420px; overflow: auto">{html}</div>',
        unsafe_allow_html=True,
    )


def _token_choices(frame: pd.DataFrame) -> tuple[_TokenChoice, ...]:
    custom_columns = [column for column in frame.columns if column not in GROWTH_COLUMNS]
    choices = [
        _TokenChoice(
            f"Well · {label}",
            GrowthDisplayNameToken(GrowthDisplayNameScope.WELL, field),
        )
        for field, label in _WELL_FIELDS
    ]
    choices.extend(
        _TokenChoice(
            f"Well · {column} (custom)",
            GrowthDisplayNameToken(GrowthDisplayNameScope.WELL, f"custom:{column}"),
        )
        for column in custom_columns
    )
    choices.extend(
        _TokenChoice(
            f"Plate · {label}",
            GrowthDisplayNameToken(GrowthDisplayNameScope.PLATE, field),
        )
        for field, label in _PLATE_FIELDS
    )
    return tuple(choices)


def _clean_value(value: object) -> object:
    if value is None:
        return None
    missing = pd.isna(value)
    if isinstance(missing, bool) and missing:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _signature_key(signature: object) -> str:
    return hashlib.sha256(repr(signature).encode()).hexdigest()[:12]
