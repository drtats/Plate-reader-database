"""Prepare growth-curve plot data without UI or database coupling."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.domain.common import DomainIssue, WellPosition
from plate_reader.domain.growth import (
    GrowthBackground,
    GrowthMeasurement,
    WellBackgroundAssignment,
    subtract_background,
)
from plate_reader.domain.growth.models import BackgroundQcStatus


@dataclass(frozen=True, slots=True)
class GrowthPlotPoint:
    position: str
    label: str
    elapsed_minutes: float
    channel: str
    value: float
    value_raw: float
    background_mean: float | None
    correction_applied: bool
    time_index: int = 0
    elapsed_microseconds: int = 0


@dataclass(frozen=True, slots=True)
class GrowthPlotData:
    points: tuple[GrowthPlotPoint, ...]
    issues: tuple[DomainIssue, ...]
    correction_requested: bool


@dataclass(frozen=True, slots=True)
class GrowthPlotLabelOptions:
    fields: tuple[str, ...] = ("display_name",)
    separator: str = "_"
    prefix: str = ""
    suffix: str = ""
    omit_empty: bool = True

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("Growth plot labels require at least one field")
        if any(not field.strip() for field in self.fields):
            raise ValueError("Growth plot label fields cannot be empty")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Growth plot label fields cannot be repeated")


@dataclass(frozen=True, slots=True)
class GrowthPlotLabelField:
    """One well-layout field that may contribute to a curve label."""

    key: str
    label: str


_STANDARD_LABEL_FIELDS = (
    GrowthPlotLabelField("position", "Well position"),
    GrowthPlotLabelField("raw_label", "Raw label"),
    GrowthPlotLabelField("display_name", "Display name"),
    GrowthPlotLabelField("is_blank", "Blank"),
    GrowthPlotLabelField("background_group", "Background group"),
    GrowthPlotLabelField("grouping_label", "Group"),
    GrowthPlotLabelField("medium", "Media"),
    GrowthPlotLabelField("strain", "Strain"),
    GrowthPlotLabelField("inoculum_size", "Inoculum size"),
    GrowthPlotLabelField("inoculum_unit", "Inoculum unit"),
    GrowthPlotLabelField("replicate", "Replicate"),
    GrowthPlotLabelField("notes", "Notes"),
    GrowthPlotLabelField("treatment", "Treatment"),
    GrowthPlotLabelField("concentration", "Concentration"),
    GrowthPlotLabelField("concentration_unit", "Concentration unit"),
)


def growth_plot_label_fields(
    wells: Sequence[Mapping[str, object]],
) -> tuple[GrowthPlotLabelField, ...]:
    """Return every standard layout label field plus discovered custom fields.

    Standard fields stay available even when every well is currently empty. This
    keeps label configuration stable across runs and leaves empty-value handling
    to :class:`GrowthPlotLabelOptions`.
    """

    custom_names = sorted(
        {name for well in wells for name in _custom_values(well)}, key=str.casefold
    )
    return (
        *_STANDARD_LABEL_FIELDS,
        *(GrowthPlotLabelField(f"custom:{name}", f"{name} (custom)") for name in custom_names),
    )


class PrepareGrowthPlotDataService:
    """Convert a loaded snapshot into explicit raw/corrected plot points."""

    def execute(
        self,
        snapshot: PlateSnapshot,
        backgrounds: tuple[dict[str, object], ...],
        selected_positions: tuple[str, ...],
        *,
        corrected: bool,
        label_field: str = "display_name",
        label_options: GrowthPlotLabelOptions | None = None,
    ) -> GrowthPlotData:
        selected = set(selected_positions)
        wells_by_id = {str(well["well_id"]): well for well in snapshot.wells}
        measurements = tuple(
            GrowthMeasurement(
                WellPosition.parse(str(wells_by_id[str(row["well_id"])]["position"])),
                _int(row["time_index"]),
                _int(row["elapsed_microseconds"]),
                str(row["channel"]),
                _float(row["value_raw"]),
            )
            for row in snapshot.raw_observations
            if str(wells_by_id[str(row["well_id"])]["position"]) in selected
        )
        selected_label_options = label_options or GrowthPlotLabelOptions((label_field,))
        labels = {
            str(well["position"]): _display_label(well, selected_label_options)
            for well in snapshot.wells
            if str(well["position"]) in selected
        }
        if not corrected:
            return GrowthPlotData(
                tuple(
                    GrowthPlotPoint(
                        measurement.position.label,
                        labels[measurement.position.label],
                        measurement.elapsed_minutes,
                        measurement.channel,
                        measurement.value_raw,
                        measurement.value_raw,
                        None,
                        False,
                        measurement.time_index,
                        measurement.elapsed_microseconds,
                    )
                    for measurement in measurements
                ),
                (),
                False,
            )

        assignments = tuple(
            WellBackgroundAssignment(
                WellPosition.parse(str(well["position"])),
                bool(well["is_blank"]),
                str(well["background_group"] or "plate"),
            )
            for well in snapshot.wells
            if str(well["position"]) in selected
        )
        typed_backgrounds = tuple(
            GrowthBackground(
                str(row["background_group"]),
                str(row["channel"]),
                _int(row["time_index"]),
                _int(row["elapsed_microseconds"]),
                _float(row["mean_value"]),
                _float(row["std_value"]),
                _float(row["coefficient_of_variation"]),
                _int(row["blank_count"]),
                BackgroundQcStatus(str(row["qc_status"])),
            )
            for row in backgrounds
        )
        correction = subtract_background(
            measurements,
            assignments,
            typed_backgrounds,
            manual_offset=_float(snapshot.metadata.get("manual_subtraction", 0.0)),
        )
        return GrowthPlotData(
            tuple(
                GrowthPlotPoint(
                    item.measurement.position.label,
                    labels[item.measurement.position.label],
                    item.measurement.elapsed_minutes,
                    item.measurement.channel,
                    (
                        item.corrected_value
                        if item.corrected_value is not None
                        else item.measurement.value_raw
                    ),
                    item.measurement.value_raw,
                    item.background_mean,
                    item.corrected_value is not None,
                    item.measurement.time_index,
                    item.measurement.elapsed_microseconds,
                )
                for item in correction.measurements
            ),
            correction.issues,
            True,
        )


def _display_label(well: dict[str, Any], options: GrowthPlotLabelOptions) -> str:
    values = tuple(_label_value(well, field) for field in options.fields)
    parts = tuple(
        text for value in values if (text := _label_text(value)) or not options.omit_empty
    )
    combined = options.separator.join(parts)
    if combined:
        return f"{options.prefix}{combined}{options.suffix}"
    return _fallback_label(well)


def _label_value(well: dict[str, Any], field: str) -> object:
    if field.startswith("custom:"):
        return _custom_values(well).get(field.removeprefix("custom:"))
    if field not in well:
        raise ValueError(f"Unknown Growth plot label field: {field}")
    return well.get(field)


def _label_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value).strip()


def _fallback_label(well: dict[str, Any]) -> str:
    for key in ("display_name", "raw_label", "position"):
        value = well.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError("Well has no displayable label")


def _custom_values(well: Mapping[str, object]) -> Mapping[str, object]:
    value = well.get("custom_json")
    if value is None or value == "":
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, Mapping):
        raise ValueError("Growth plot label custom metadata must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer database value")
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expected a numeric database value")
    return float(value)
