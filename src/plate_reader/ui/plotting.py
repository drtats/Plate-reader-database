"""Cached Plotly builders with no database access."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from plate_reader.application.services import GrowthPlotData


@dataclass(frozen=True, slots=True)
class GrowthPlotOptions:
    title: str = ""
    x_max: float = 1_400.0
    y_min: float = 0.001
    y_max: float = 1.5
    symlog: bool = True

    def __post_init__(self) -> None:
        values = (self.x_max, self.y_min, self.y_max)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Plot limits must be finite")
        if self.x_max <= 0:
            raise ValueError("X maximum must be greater than zero")
        if self.y_min >= self.y_max:
            raise ValueError("Y minimum must be less than Y maximum")


@st.cache_data(show_spinner="Preparing growth curves…")
def growth_curve_figure(
    plot_data: GrowthPlotData,
    options: GrowthPlotOptions,
    raw_hash: str,
    revision_key: str,
) -> go.Figure:
    del raw_hash, revision_key
    records = [
        {
            "Time (minutes)": point.elapsed_minutes,
            "OD": point.value,
            "Plot value": _symlog(point.value) if options.symlog else point.value,
            "Curve": (
                point.position
                if point.label == point.position
                else f"{point.label} ({point.position})"
            ),
            "Channel": point.channel,
            "Correction": (
                "corrected"
                if point.correction_applied
                else "raw fallback"
                if plot_data.correction_requested
                else "raw"
            ),
        }
        for point in plot_data.points
    ]
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return go.Figure().update_layout(title="No wells selected")
    figure = px.line(
        frame,
        x="Time (minutes)",
        y="Plot value",
        color="Curve",
        hover_data={"OD": ":.5g", "Channel": True, "Correction": True, "Plot value": False},
        render_mode="webgl",
    )
    figure.update_layout(title=options.title or None)
    figure.update_xaxes(range=(0, options.x_max), title="Time (minutes)")
    if options.symlog:
        ticks = _symlog_ticks(options.y_min, options.y_max)
        figure.update_yaxes(
            range=(_symlog(options.y_min), _symlog(options.y_max)),
            tickmode="array",
            tickvals=[_symlog(value) for value in ticks],
            ticktext=[f"{value:g}" for value in ticks],
            title="OD (symmetric log)",
        )
    else:
        figure.update_yaxes(range=(options.y_min, options.y_max), title="OD")
    return figure


def plot_download_config(title: str, plate_id: str) -> dict[str, object]:
    filename_source = title.strip() or f"growth-plot-{plate_id}"
    filename = "-".join(filename_source.lower().split())
    safe_filename = "".join(
        character for character in filename if character.isalnum() or character in "-_"
    )
    return {
        "displaylogo": False,
        "toImageButtonOptions": {
            "format": "png",
            "filename": safe_filename or "growth-plot",
            "width": 1_200,
            "height": 750,
            "scale": 2,
        },
    }


def _symlog(value: float, *, linear_threshold: float = 0.01) -> float:
    return math.copysign(math.log10(1 + abs(value) / linear_threshold), value)


def _symlog_ticks(minimum: float, maximum: float) -> tuple[float, ...]:
    candidates = (
        -100.0,
        -10.0,
        -1.0,
        -0.1,
        -0.01,
        -0.001,
        0.0,
        0.001,
        0.01,
        0.1,
        1.0,
        10.0,
        100.0,
    )
    ticks = tuple(value for value in candidates if minimum <= value <= maximum)
    return ticks or (minimum, maximum)


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
