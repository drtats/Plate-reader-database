"""Pure selected Growth plot-data export."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from plate_reader.application.services.growth_plot_styles import GrowthPlotStyles
from plate_reader.application.services.growth_plotting import GrowthPlotData

_WELL_FIELDS = (
    ("grouping_label", "Group"),
    ("medium", "Media"),
    ("strain", "Strain"),
    ("inoculum_size", "Inoculum size"),
    ("inoculum_unit", "Inoculum unit"),
    ("replicate", "Replicate"),
    ("treatment", "Treatment"),
    ("concentration", "Concentration"),
    ("concentration_unit", "Concentration unit"),
    ("notes", "Notes"),
)


@dataclass(frozen=True, slots=True)
class GrowthDataExportContext:
    plate_id: str
    experiment_name: str
    plate_name: str
    revision_id: str

    def __post_init__(self) -> None:
        if not self.plate_id.strip():
            raise ValueError("Growth data export plate ID cannot be empty")
        if not self.revision_id.strip():
            raise ValueError("Growth data export revision identity cannot be empty")


@dataclass(frozen=True, slots=True)
class GrowthDataCsvArtifact:
    filename: str
    content: bytes
    row_count: int


def export_growth_plot_data_csv(
    plot_data: GrowthPlotData,
    wells: Sequence[Mapping[str, object]],
    context: GrowthDataExportContext,
    filename_source: str,
) -> GrowthDataCsvArtifact:
    """Export the exact prepared plot points without reloading or recalculating."""

    by_position = _wells_by_position(wells)
    plotted_positions = {point.position for point in plot_data.points}
    if not plotted_positions.issubset(by_position):
        raise ValueError("Growth plot data contains a well outside the supplied layout")
    custom_columns = _custom_columns(wells)
    headers = (
        "Plate ID",
        "Experiment name",
        "Plate name",
        "Background revision",
        "Well",
        "Display name",
        *(label for _key, label in _WELL_FIELDS),
        *(f"Custom: {name}" for name in custom_columns),
        "Channel",
        "Time index",
        "Elapsed microseconds",
        "Elapsed minutes",
        "Raw value",
        "Background mean",
        "Plotted value",
        "Correction applied",
    )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    for point in plot_data.points:
        well = by_position[point.position]
        custom = _custom_values(well)
        writer.writerow(
            (
                context.plate_id,
                context.experiment_name,
                context.plate_name,
                context.revision_id,
                point.position,
                _display_name(well, point.position),
                *(well.get(key) for key, _label in _WELL_FIELDS),
                *(custom.get(name) for name in custom_columns),
                point.channel,
                point.time_index,
                point.elapsed_microseconds,
                point.elapsed_minutes,
                point.value_raw,
                point.background_mean,
                point.value,
                point.correction_applied,
            )
        )
    safe_name = _safe_filename(filename_source) or _safe_filename(f"growth-plot-{context.plate_id}")
    return GrowthDataCsvArtifact(
        f"{safe_name}-data.csv",
        stream.getvalue().encode("utf-8-sig"),
        len(plot_data.points),
    )


def export_growth_plot_wide_csv(
    plot_data: GrowthPlotData,
    styles: GrowthPlotStyles,
    filename_source: str,
) -> GrowthDataCsvArtifact:
    """Export one time column followed by one column per visible plot series."""

    series_keys = tuple((style.position, style.channel) for style in styles.styles)
    if len(set(series_keys)) != len(series_keys):
        raise ValueError("Growth wide export styles contain duplicate series")
    if set(series_keys) != {(point.position, point.channel) for point in plot_data.points}:
        raise ValueError("Growth wide export styles do not match prepared series")
    values: dict[tuple[int, int, float], dict[tuple[str, str], float]] = {}
    for point in plot_data.points:
        time_key = (point.time_index, point.elapsed_microseconds, point.elapsed_minutes)
        series = values.setdefault(time_key, {})
        series_key = (point.position, point.channel)
        if series_key in series:
            raise ValueError("Growth wide export contains duplicate series/time points")
        series[series_key] = point.value

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("Time (minutes)", *(style.legend_label for style in styles.styles)))
    for time_key in sorted(values):
        row = values[time_key]
        writer.writerow((time_key[2], *(row.get(series_key) for series_key in series_keys)))
    safe_name = _safe_filename(filename_source) or "growth-plot"
    return GrowthDataCsvArtifact(
        f"{safe_name}-wide.csv",
        stream.getvalue().encode("utf-8-sig"),
        len(values),
    )


def _wells_by_position(
    wells: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for well in wells:
        position = str(well.get("position") or "").strip()
        if not position:
            raise ValueError("Growth data export well position cannot be empty")
        if position in result:
            raise ValueError(f"Duplicate Growth data export well: {position}")
        result[position] = well
    return result


def _custom_columns(wells: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {name for well in wells for name in _custom_values(well)},
            key=str.casefold,
        )
    )


def _custom_values(well: Mapping[str, object]) -> Mapping[str, object]:
    value = well.get("custom_json")
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as error:
        raise ValueError("Growth data export custom metadata must be valid JSON") from error
    if not isinstance(parsed, Mapping):
        raise ValueError("Growth data export custom metadata must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _display_name(well: Mapping[str, object], position: str) -> str:
    for key in ("display_name", "raw_label"):
        value = well.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return position


def _safe_filename(value: str) -> str:
    normalized = "-".join(value.strip().lower().split())
    return "".join(
        character for character in normalized if character.isalnum() or character in "-_"
    )
