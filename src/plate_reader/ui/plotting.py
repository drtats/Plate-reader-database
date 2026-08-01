"""Cached Plotly builders with no database access."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from plate_reader.application.services import GrowthPlotData, GrowthPlotPoint


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


@st.cache_data(show_spinner="Preparing the 96-well curve overview…")
def growth_plate_overview_figure(
    plot_data: GrowthPlotData,
    raw_hash: str,
    revision_key: str,
) -> go.Figure:
    """Build the legacy 8x12 curve overview on demand with shared axes."""

    del raw_hash, revision_key
    positions = tuple(f"{row}{column}" for row in "ABCDEFGH" for column in range(1, 13))
    figure = make_subplots(
        rows=8,
        cols=12,
        shared_xaxes=True,
        shared_yaxes=True,
        horizontal_spacing=0.004,
        vertical_spacing=0.018,
        subplot_titles=positions,
    )
    by_curve: dict[tuple[str, str], list[GrowthPlotPoint]] = {}
    for point in plot_data.points:
        by_curve.setdefault((point.position, point.channel), []).append(point)
    for index, position in enumerate(positions):
        row = index // 12 + 1
        column = index % 12 + 1
        channels = sorted(channel for well, channel in by_curve if well == position)
        for channel in channels:
            points = sorted(by_curve[(position, channel)], key=lambda point: point.elapsed_minutes)
            figure.add_trace(
                go.Scattergl(
                    x=[point.elapsed_minutes for point in points],
                    y=[point.value for point in points],
                    customdata=[(point.label, point.value_raw, point.channel) for point in points],
                    mode="lines",
                    line={"width": 1},
                    fill="tozeroy",
                    fillcolor="rgba(31, 119, 180, 0.18)",
                    hovertemplate=(
                        "%{customdata[0]}<br>Time: %{x:.1f} min<br>"
                        "OD: %{y:.5g}<br>Raw: %{customdata[1]:.5g}<br>"
                        "Channel: %{customdata[2]}<extra></extra>"
                    ),
                    showlegend=False,
                ),
                row=row,
                col=column,
            )
    figure.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
    figure.update_yaxes(showticklabels=False, showgrid=False, zeroline=False)
    figure.update_annotations(font={"size": 8})
    state = "background-corrected" if plot_data.correction_requested else "raw"
    figure.update_layout(
        title=f"96-well growth curves ({state})",
        height=900,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
    )
    return figure


def plot_download_config(
    title: str,
    plate_id: str,
    *,
    width: int = 1_200,
    height: int = 750,
) -> dict[str, object]:
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
            "width": width,
            "height": height,
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
