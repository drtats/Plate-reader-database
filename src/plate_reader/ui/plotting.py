"""Cached Plotly builders with no database access."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services import PrepareGrowthPlotDataService


@st.cache_data(show_spinner="Preparing growth curves…")
def growth_curve_figure(
    snapshot: PlateSnapshot,
    backgrounds: Sequence[dict[str, object]],
    selected_positions: tuple[str, ...],
    corrected: bool,
    raw_hash: str,
    revision_key: str,
) -> go.Figure:
    del raw_hash, revision_key
    plot_data = PrepareGrowthPlotDataService().execute(
        snapshot,
        tuple(backgrounds),
        selected_positions,
        corrected=corrected,
    )
    records = [
        {
            "Time (minutes)": point.elapsed_minutes,
            "OD": point.value,
            "Well": point.position,
        }
        for point in plot_data.points
    ]
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return go.Figure().update_layout(title="No wells selected")
    return px.line(frame, x="Time (minutes)", y="OD", color="Well", render_mode="webgl")


@st.cache_data(show_spinner=False)
def endpoint_heatmap(
    raw_rows: Sequence[dict[str, object]], wells: Sequence[dict[str, object]], raw_hash: str
) -> go.Figure:
    del raw_hash
    well_by_id = {str(well["well_id"]): well for well in wells}
    last_by_well: dict[str, tuple[int, float]] = {}
    for row in raw_rows:
        well_id = str(row["well_id"])
        time_index = int_value(row["time_index"])
        if well_id not in last_by_well or time_index > last_by_well[well_id][0]:
            last_by_well[well_id] = (time_index, float_value(row["value_raw"]))
    matrix: list[list[float | None]] = [[None for _ in range(12)] for _ in range(8)]
    labels = [["" for _ in range(12)] for _ in range(8)]
    for well_id, (_time_index, value) in last_by_well.items():
        well = well_by_id[well_id]
        row_index = int_value(well["row_index"])
        column_index = int_value(well["column_index"])
        matrix[row_index][column_index] = value
        labels[row_index][column_index] = str(well["position"])
    return px.imshow(
        matrix,
        x=list(range(1, 13)),
        y=list("ABCDEFGH"),
        labels={"x": "Column", "y": "Row", "color": "Final OD"},
        text_auto=".3f",
        aspect="auto",
        color_continuous_scale="Viridis",
    ).update_traces(customdata=labels, hovertemplate="%{customdata}: %{z:.4f}<extra></extra>")


@st.cache_data(show_spinner=False)
def mic_plate_heatmap(
    raw_rows: Sequence[dict[str, object]], wells: Sequence[dict[str, object]], raw_hash: str
) -> go.Figure:
    del raw_hash
    well_by_id = {str(well["well_id"]): well for well in wells}
    matrix: list[list[float | None]] = [[None for _ in range(12)] for _ in range(8)]
    labels = [["" for _ in range(12)] for _ in range(8)]
    for reading in raw_rows:
        well = well_by_id[str(reading["well_id"])]
        row_index = int_value(well["row_index"])
        column_index = int_value(well["column_index"])
        matrix[row_index][column_index] = float_value(reading["value_raw"])
        labels[row_index][column_index] = str(well["position"])
    return px.imshow(
        matrix,
        x=list(range(1, 13)),
        y=list("ABCDEFGH"),
        labels={"x": "Column", "y": "Row", "color": "Raw OD"},
        text_auto=".3f",
        aspect="auto",
        color_continuous_scale="Viridis",
    ).update_traces(customdata=labels, hovertemplate="%{customdata}: %{z:.4f}<extra></extra>")


@st.cache_data(show_spinner=False)
def mic_growth_map(
    wells: Sequence[dict[str, object]],
    calls: Sequence[dict[str, object]],
    revision_key: str,
) -> go.Figure:
    del revision_key
    well_by_id = {str(well["well_id"]): well for well in wells}
    matrix: list[list[int | None]] = [[None for _ in range(12)] for _ in range(8)]
    labels = [["" for _ in range(12)] for _ in range(8)]
    for call in calls:
        well = well_by_id[str(call["well_id"])]
        row_index = int_value(well["row_index"])
        column_index = int_value(well["column_index"])
        value = call.get("growth_call")
        matrix[row_index][column_index] = None if value is None else int(bool(value))
        labels[row_index][column_index] = str(well["position"])
    return px.imshow(
        matrix,
        x=list(range(1, 13)),
        y=list("ABCDEFGH"),
        labels={"x": "Column", "y": "Row", "color": "Growth call"},
        text_auto=True,
        aspect="auto",
        color_continuous_scale=((0.0, "#d9f0d3"), (1.0, "#d73027")),
        zmin=0,
        zmax=1,
    ).update_traces(customdata=labels, hovertemplate="%{customdata}: %{z}<extra></extra>")


@st.cache_data(show_spinner="Preparing MIC plot…")
def mic_result_dot_plot(results: Sequence[dict[str, object]], result_key: str) -> go.Figure:
    del result_key
    frame = pd.DataFrame.from_records(results)
    if frame.empty:
        return go.Figure().update_layout(title="No MIC results match the filters")
    return px.scatter(
        frame,
        x="treatment",
        y="mic_value",
        color="strain",
        symbol="mic_operator",
        hover_data=("medium", "replicate", "mic_unit", "plate_name", "experiment_date"),
        labels={"treatment": "Treatment", "mic_value": "MIC"},
    )


def int_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer database value")
    return value


def float_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expected a numeric database value")
    return float(value)
