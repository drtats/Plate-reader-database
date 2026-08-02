"""Deterministic series identity and colors shared by Growth renderers."""

from __future__ import annotations

import colorsys
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from plate_reader.application.services.growth_plotting import GrowthPlotData
from plate_reader.domain.common.plate import PLATE_96, WellPosition

_CUSTOM_PREFIX = "custom:"


class GrowthPlotColorMode(StrEnum):
    RAINBOW_PLATE_ORDER = "rainbow_plate_order"
    RAINBOW_SERIES_ORDER = "rainbow_series_order"
    CATEGORICAL = "categorical"


@dataclass(frozen=True, slots=True)
class GrowthPlotColorOptions:
    mode: GrowthPlotColorMode = GrowthPlotColorMode.RAINBOW_PLATE_ORDER
    field: str | None = None

    def __post_init__(self) -> None:
        if self.mode is GrowthPlotColorMode.CATEGORICAL and not str(self.field or "").strip():
            raise ValueError("Categorical Growth plot colors require a metadata field")
        if self.mode is not GrowthPlotColorMode.CATEGORICAL and self.field is not None:
            raise ValueError("Rainbow Growth plot colors cannot specify a metadata field")


@dataclass(frozen=True, slots=True)
class GrowthSeriesStyle:
    position: str
    channel: str
    legend_label: str
    color_hex: str
    color_group: str


@dataclass(frozen=True, slots=True)
class GrowthPlotStyles:
    styles: tuple[GrowthSeriesStyle, ...]
    options: GrowthPlotColorOptions


class BuildGrowthPlotStylesService:
    """Assign stable colors to every prepared `(well, channel)` series."""

    def execute(
        self,
        plot_data: GrowthPlotData,
        wells: Sequence[Mapping[str, object]],
        options: GrowthPlotColorOptions,
    ) -> GrowthPlotStyles:
        by_position = _validated_wells(wells)
        series = _series_in_first_appearance(plot_data)
        unknown_positions = {position for position, _channel, _label in series} - set(by_position)
        if unknown_positions:
            raise ValueError("Growth plot data contains a well outside the supplied layout")
        if options.mode is GrowthPlotColorMode.RAINBOW_PLATE_ORDER:
            ordered = tuple(sorted(series, key=_physical_series_key))
            colors = _rainbow_colors(len(ordered))
            color_by_series = {key: colors[index] for index, key in enumerate(ordered)}
            group_by_series = {key: key[0] for key in ordered}
        elif options.mode is GrowthPlotColorMode.RAINBOW_SERIES_ORDER:
            colors = _rainbow_colors(len(series))
            color_by_series = {key: colors[index] for index, key in enumerate(series)}
            group_by_series = {key: f"series-{index + 1}" for index, key in enumerate(series)}
            ordered = series
        else:
            assert options.field is not None
            categories = {
                key: _category_value(by_position[key[0]], options.field) for key in series
            }
            ordered_categories = tuple(
                sorted(set(categories.values()), key=lambda value: value.casefold())
            )
            category_colors = dict(
                zip(ordered_categories, _rainbow_colors(len(ordered_categories)), strict=True)
            )
            color_by_series = {key: category_colors[categories[key]] for key in series}
            group_by_series = categories
            ordered = tuple(sorted(series, key=_physical_series_key))

        channels = {channel for _position, channel, _label in series}
        positions_by_label: dict[str, set[str]] = {}
        for position, _channel, label in series:
            positions_by_label.setdefault(label, set()).add(position)
        duplicate_labels = {
            label for label, positions in positions_by_label.items() if len(positions) > 1
        }
        label_by_key = {(position, channel): label for position, channel, label in series}
        return GrowthPlotStyles(
            tuple(
                GrowthSeriesStyle(
                    position,
                    channel,
                    _legend_label(
                        position,
                        label_by_key[(position, channel)],
                        channel,
                        include_channel=len(channels) > 1,
                        include_position=label_by_key[(position, channel)] in duplicate_labels,
                    ),
                    color_by_series[(position, channel, label_by_key[(position, channel)])],
                    group_by_series[(position, channel, label_by_key[(position, channel)])],
                )
                for position, channel, _label in ordered
            ),
            options,
        )


def default_growth_plot_styles(plot_data: GrowthPlotData) -> GrowthPlotStyles:
    """Build a metadata-free stable fallback for direct renderer callers."""

    series = _series_in_first_appearance(plot_data)
    colors = _rainbow_colors(len(series))
    channels = {channel for _position, channel, _label in series}
    positions_by_label: dict[str, set[str]] = {}
    for position, _channel, label in series:
        positions_by_label.setdefault(label, set()).add(position)
    return GrowthPlotStyles(
        tuple(
            GrowthSeriesStyle(
                position,
                channel,
                _legend_label(
                    position,
                    label,
                    channel,
                    include_channel=len(channels) > 1,
                    include_position=len(positions_by_label[label]) > 1,
                ),
                colors[index],
                f"series-{index + 1}",
            )
            for index, (position, channel, label) in enumerate(series)
        ),
        GrowthPlotColorOptions(GrowthPlotColorMode.RAINBOW_SERIES_ORDER),
    )


def _series_in_first_appearance(
    plot_data: GrowthPlotData,
) -> tuple[tuple[str, str, str], ...]:
    seen: set[tuple[str, str]] = set()
    result = []
    for point in plot_data.points:
        key = (point.position, point.channel)
        if key not in seen:
            seen.add(key)
            result.append((point.position, point.channel, point.label))
    return tuple(result)


def _validated_wells(
    wells: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    expected = {position.label for position in PLATE_96.positions()}
    by_position: dict[str, Mapping[str, object]] = {}
    for well in wells:
        position = WellPosition.parse(str(well.get("position", ""))).label
        if position in by_position:
            raise ValueError(f"Duplicate Growth plot-style well: {position}")
        by_position[position] = well
    if set(by_position) != expected:
        raise ValueError("Growth plot styles require every A1-H12 well")
    return by_position


def _category_value(well: Mapping[str, object], field: str) -> str:
    if field.startswith(_CUSTOM_PREFIX):
        value = _custom_values(well).get(field.removeprefix(_CUSTOM_PREFIX))
    else:
        if field not in well:
            raise ValueError(f"Unknown Growth plot color field: {field}")
        value = well.get(field)
    text = str(value).strip() if value is not None else ""
    return text or "(empty)"


def _custom_values(well: Mapping[str, object]) -> Mapping[str, object]:
    value = well.get("custom_json")
    if value is None or value == "":
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, Mapping):
        raise ValueError("Growth plot color custom metadata must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _physical_series_key(series: tuple[str, str, str]) -> tuple[int, int, str]:
    position = WellPosition.parse(series[0])
    return (position.row_index, position.column_index, series[1].casefold())


def _legend_label(
    position: str,
    label: str,
    channel: str,
    *,
    include_channel: bool,
    include_position: bool,
) -> str:
    base = f"{label} ({position})" if include_position else label
    return f"{base} · {channel}" if include_channel else base


def _rainbow_colors(count: int) -> tuple[str, ...]:
    if count < 1:
        return ()
    return tuple(_hsv_hex(index / count) for index in range(count))


def _hsv_hex(hue: float) -> str:
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.82)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"
