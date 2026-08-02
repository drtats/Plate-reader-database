"""Friendly read-only Growth background and activity history views."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True, slots=True)
class GrowthBackgroundHistoryItem:
    status: str
    calculated_by: str
    calculated_at: str
    method: str
    details: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GrowthActivityItem:
    action: str
    summary: str
    user: str
    timestamp: str
    details: Mapping[str, object]


_ACTIVITY_LABELS = {
    "growth_imported": "Growth run imported",
    "legacy_growth_imported": "Legacy Growth run imported",
    "growth_metadata_updated": "Metadata updated",
    "growth_layout_updated": "Well layout or selection updated",
    "growth_background_computed": "Background calculation saved",
    "growth_exported": "Growth run exported",
}


def growth_background_history_items(
    revisions: Sequence[Mapping[str, object]],
    *,
    current_is_stale: bool,
) -> tuple[GrowthBackgroundHistoryItem, ...]:
    """Present every Growth background revision without discarding raw fields."""

    result = []
    for revision in reversed(revisions):
        if str(revision.get("algorithm_name")) != "growth_background":
            continue
        is_current = bool(revision.get("is_current"))
        status = (
            "Current · stale — recompute required"
            if is_current and current_is_stale
            else "Current · ready"
            if is_current
            else "Previous calculation"
        )
        result.append(
            GrowthBackgroundHistoryItem(
                status=status,
                calculated_by=str(revision.get("created_by") or "Unknown user"),
                calculated_at=str(revision.get("created_at") or "Unknown time"),
                method=(
                    "Time-course background · "
                    f"{revision.get('algorithm_version') or 'unknown version'}"
                ),
                details=dict(revision),
            )
        )
    return tuple(result)


def growth_activity_items(
    records: Sequence[Mapping[str, object]],
) -> tuple[GrowthActivityItem, ...]:
    """Present every append-only event with friendly leading fields."""

    return tuple(
        GrowthActivityItem(
            action=_friendly_action(str(record.get("event_type") or "activity_recorded")),
            summary=_activity_summary(record),
            user=str(record.get("actor_id") or "Unknown user"),
            timestamp=str(record.get("occurred_at") or "Unknown time"),
            details=dict(record),
        )
        for record in reversed(records)
    )


def render_growth_background_history(
    revisions: Sequence[Mapping[str, object]],
    *,
    current_is_stale: bool,
) -> None:
    st.subheader("Background history")
    st.write(
        "Background history is the receipt for blank subtraction: a saved, versioned calculation. "
        "Each revision records "
        "which blank wells and groups were used to calculate a baseline at every timepoint. "
        "It never changes the imported raw measurements."
    )
    st.caption(
        "Current means the calculation matches today's plate layout. Stale means blank or "
        "background-group assignments changed after it was calculated; recompute from "
        "Overview & QC before using corrected curves. Previous calculations remain visible "
        "for traceability."
    )
    items = growth_background_history_items(revisions, current_is_stale=current_is_stale)
    if current_is_stale:
        st.warning(
            "The current calculation is stale because blank or background-group assignments "
            "changed. Corrected plots use raw fallback values until you recompute it from "
            "Overview & QC."
        )
    elif items:
        st.success("The current background calculation matches the saved well assignments.")
    else:
        st.info(
            "No background calculation has been saved. Plots use raw values until one is "
            "computed from Overview & QC."
        )
    if items:
        st.markdown(
            _markdown_table(
                ("Status", "Calculated by", "Calculated at", "Method"),
                tuple(
                    (item.status, item.calculated_by, item.calculated_at, item.method)
                    for item in items
                ),
            )
        )
    with st.expander("Technical background revision details", expanded=False):
        if not items:
            st.caption("No technical revision records are available.")
        for item in items:
            st.json(dict(item.details))


def render_growth_activity_log(records: Sequence[Mapping[str, object]]) -> None:
    st.subheader("Activity log")
    st.write(
        "This append-only audit log records saved actions such as imports, edits, and "
        "background recalculations, with the user and time. Technical IDs and stored "
        "payloads remain available below."
    )
    items = growth_activity_items(records)
    if not items:
        st.info("No activity has been recorded for this run.")
    else:
        st.markdown(
            _markdown_table(
                ("Action", "What changed", "User", "When"),
                tuple((item.action, item.summary, item.user, item.timestamp) for item in items),
            )
        )
    with st.expander("Technical activity details", expanded=False):
        if not items:
            st.caption("No technical activity records are available.")
        for item in items:
            st.json(dict(item.details))


def _friendly_action(event_type: str) -> str:
    return _ACTIVITY_LABELS.get(event_type, event_type.replace("_", " ").strip().capitalize())


_FIELD_LABELS = {
    "name": "Experiment name",
    "plate_name": "Plate name",
    "experiment_date": "Experiment date",
    "operator_name": "User",
    "custom_json": "Custom metadata",
    "display_name": "Display name",
    "is_blank": "Blank",
    "background_group": "Background group",
    "grouping_label": "Group",
    "plot_selected": "Plot selection",
    "manual_subtraction": "Global subtraction",
    "lifecycle_status": "Lifecycle",
}


def _activity_summary(record: Mapping[str, object]) -> str:
    details = _details_mapping(record.get("details_json"))
    changes = details.get("changes")
    if isinstance(changes, list):
        summaries = [_change_summary(change) for change in changes if isinstance(change, Mapping)]
        summaries = [summary for summary in summaries if summary]
        if summaries:
            visible = "; ".join(summaries[:4])
            remaining = len(summaries) - 4
            return f"{visible}; +{remaining} more" if remaining > 0 else visible
        if str(record.get("event_type")) in {
            "growth_metadata_updated",
            "growth_layout_updated",
        }:
            return "Saved; no stored values changed"
    if (positions := details.get("positions")) and isinstance(positions, list):
        visible = ", ".join(str(item) for item in positions[:6])
        return f"Saved {len(positions)} well(s): {visible}"
    if (fields := details.get("plate_fields") or details.get("experiment_fields")) and isinstance(
        fields, list
    ):
        return "Saved " + ", ".join(_field_label(str(item)) for item in fields)
    return "—"


def _change_summary(change: Mapping[str, object]) -> str:
    field = _field_label(str(change.get("field") or "field"))
    position = str(change.get("position") or "").strip()
    prefix = f"{position} {field}" if position else field
    return f"{prefix}: {_short_value(change.get('before'))} → {_short_value(change.get('after'))}"


def _details_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _field_label(field: str) -> str:
    return _FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def _short_value(value: object) -> str:
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Mapping | list):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    return text if len(text) <= 40 else f"{text[:37]}…"


def _markdown_table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _header in headers) + " |"
    body = tuple("| " + " | ".join(_markdown_cell(value) for value in row) + " |" for row in rows)
    return "\n".join((header, separator, *body))


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
