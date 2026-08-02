"""Growth Overview heatmap controls with exact channel/timepoint identity."""

from __future__ import annotations

import streamlit as st

from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services import (
    BuildGrowthHeatmapCatalogService,
    GrowthHeatmapData,
    GrowthHeatmapTimepoint,
    PrepareGrowthHeatmapDataService,
)
from plate_reader.ui.plotting import growth_heatmap_figure


def render_growth_heatmap(
    snapshot: PlateSnapshot,
    backgrounds: tuple[dict[str, object], ...],
    raw_hash: str,
    revision_key: str,
) -> None:  # pragma: no cover - Streamlit widget composition
    catalog = BuildGrowthHeatmapCatalogService().execute(snapshot)
    if not catalog.channels:
        st.info("No Growth observations are available for a heatmap.")
        return
    channels = tuple(item.channel for item in catalog.channels)
    saved_channel = str(snapshot.metadata.get("channel") or "")
    channel = st.selectbox(
        "Heatmap channel",
        channels,
        index=channels.index(saved_channel) if saved_channel in channels else 0,
        key=f"growth_heatmap_channel_{snapshot.plate_id}",
    )
    timepoints = catalog.timepoints_for(channel)
    timepoint = st.selectbox(
        "Heatmap timepoint",
        timepoints,
        index=len(timepoints) - 1,
        format_func=_timepoint_label,
        key=f"growth_heatmap_timepoint_{snapshot.plate_id}_{channel}",
    )
    value_state = st.selectbox(
        "Heatmap values",
        ("Raw", "Background corrected"),
        key=f"growth_heatmap_values_{snapshot.plate_id}",
    )
    corrected = value_state == "Background corrected"
    data = prepare_growth_heatmap_data(
        snapshot,
        backgrounds,
        channel,
        timepoint,
        corrected,
        raw_hash,
        revision_key,
    )
    for issue in data.issues:
        st.warning(issue.message)
    st.plotly_chart(
        growth_heatmap_figure(
            data,
            raw_hash,
            revision_key,
            channel,
            timepoint.time_index,
            timepoint.elapsed_microseconds,
        ),
        width="stretch",
    )
    if corrected and any(not cell.correction_applied for cell in data.cells):
        state = "background correction requested; missing exact backgrounds use raw fallback"
    elif corrected:
        state = "background corrected"
    else:
        state = "raw"
    st.caption(
        f"Heatmap: {channel} · {timepoint.elapsed_minutes:g} minutes "
        f"(time index {timepoint.time_index}) · {state}."
    )


@st.cache_data(show_spinner="Preparing Growth heatmap…")
def prepare_growth_heatmap_data(
    snapshot: PlateSnapshot,
    backgrounds: tuple[dict[str, object], ...],
    channel: str,
    timepoint: GrowthHeatmapTimepoint,
    corrected: bool,
    raw_hash: str,
    revision_key: str,
) -> GrowthHeatmapData:
    """Cache scientific preparation by immutable raw/revision/time identity."""

    del raw_hash, revision_key
    return PrepareGrowthHeatmapDataService().execute(
        snapshot,
        backgrounds,
        channel,
        timepoint,
        corrected=corrected,
    )


def _timepoint_label(timepoint: GrowthHeatmapTimepoint) -> str:
    return f"{timepoint.elapsed_minutes:g} min (index {timepoint.time_index})"
