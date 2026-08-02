"""Friendly read-only Growth background and activity history views."""

from __future__ import annotations

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
        "A background revision is a saved, versioned calculation over immutable raw "
        "measurements and the blank/background-group assignments used at that time."
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
                ("Action", "User", "When"),
                tuple((item.action, item.user, item.timestamp) for item in items),
            )
        )
    with st.expander("Technical activity details", expanded=False):
        if not items:
            st.caption("No technical activity records are available.")
        for item in items:
            st.json(dict(item.details))


def _friendly_action(event_type: str) -> str:
    return _ACTIVITY_LABELS.get(event_type, event_type.replace("_", " ").strip().capitalize())


def _markdown_table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _header in headers) + " |"
    body = tuple("| " + " | ".join(_markdown_cell(value) for value in row) + " |" for row in rows)
    return "\n".join((header, separator, *body))


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
