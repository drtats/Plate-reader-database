from __future__ import annotations

from collections.abc import Mapping

import pytest

from plate_reader.application.services import (
    BuildGrowthPlotStylesService,
    GrowthPlotColorMode,
    GrowthPlotColorOptions,
    GrowthPlotData,
    GrowthPlotPoint,
)
from plate_reader.domain.common.plate import PLATE_96


def test_plate_order_is_physical_and_duplicate_labels_remain_distinct() -> None:
    data = _plot_data(("B1", "A2", "A1"), label="sample")

    result = BuildGrowthPlotStylesService().execute(
        data,
        _wells(),
        GrowthPlotColorOptions(GrowthPlotColorMode.RAINBOW_PLATE_ORDER),
    )

    assert tuple(style.position for style in result.styles) == ("A1", "A2", "B1")
    assert tuple(style.legend_label for style in result.styles) == (
        "sample (A1)",
        "sample (A2)",
        "sample (B1)",
    )
    assert len({style.color_hex for style in result.styles}) == 3


def test_series_order_and_multichannel_identity_are_stable() -> None:
    data = GrowthPlotData(
        (
            _point("B1", "sample", "gfp"),
            _point("A1", "sample", "od600"),
            _point("B1", "sample", "od600"),
        ),
        (),
        False,
    )
    options = GrowthPlotColorOptions(GrowthPlotColorMode.RAINBOW_SERIES_ORDER)

    first = BuildGrowthPlotStylesService().execute(data, _wells(), options)
    second = BuildGrowthPlotStylesService().execute(data, _wells(), options)

    assert first == second
    assert tuple((style.position, style.channel) for style in first.styles) == (
        ("B1", "gfp"),
        ("A1", "od600"),
        ("B1", "od600"),
    )
    assert tuple(style.legend_label for style in first.styles) == (
        "sample (B1) · gfp",
        "sample (A1) · od600",
        "sample (B1) · od600",
    )


@pytest.mark.parametrize("field", ["strain", "custom:Oxygen"])
def test_categorical_colors_match_metadata_categories(field: str) -> None:
    wells = _wells(
        {
            "A1": {"strain": "alpha", "custom_json": '{"Oxygen":"low"}'},
            "A2": {"strain": "alpha", "custom_json": '{"Oxygen":"low"}'},
            "B1": {"strain": "beta", "custom_json": '{"Oxygen":"high"}'},
        }
    )

    result = BuildGrowthPlotStylesService().execute(
        _plot_data(("B1", "A2", "A1")),
        wells,
        GrowthPlotColorOptions(GrowthPlotColorMode.CATEGORICAL, field),
    )
    colors = {style.position: style.color_hex for style in result.styles}

    assert colors["A1"] == colors["A2"]
    assert colors["A1"] != colors["B1"]
    assert tuple(style.position for style in result.styles) == ("A1", "A2", "B1")


def test_style_options_and_layout_validation_fail_clearly() -> None:
    with pytest.raises(ValueError, match="require a metadata field"):
        GrowthPlotColorOptions(GrowthPlotColorMode.CATEGORICAL)
    with pytest.raises(ValueError, match="cannot specify"):
        GrowthPlotColorOptions(GrowthPlotColorMode.RAINBOW_PLATE_ORDER, "strain")
    with pytest.raises(ValueError, match="every A1-H12"):
        BuildGrowthPlotStylesService().execute(
            _plot_data(("A1",)),
            _wells()[:-1],
            GrowthPlotColorOptions(),
        )
    with pytest.raises(ValueError, match="Unknown Growth plot color field"):
        BuildGrowthPlotStylesService().execute(
            _plot_data(("A1",)),
            _wells(),
            GrowthPlotColorOptions(GrowthPlotColorMode.CATEGORICAL, "missing"),
        )


def _plot_data(positions: tuple[str, ...], *, label: str = "sample") -> GrowthPlotData:
    points = tuple(_point(position, label, "od600") for position in positions)
    return GrowthPlotData(points, (), False)


def _point(position: str, label: str, channel: str) -> GrowthPlotPoint:
    return GrowthPlotPoint(position, label, 0.0, channel, 0.1, 0.1, None, False)


def _wells(
    overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[Mapping[str, object], ...]:
    selected = overrides or {}
    return tuple(
        {
            "position": position.label,
            "display_name": position.label,
            "custom_json": "{}",
            **selected.get(position.label, {}),
        }
        for position in PLATE_96.positions()
    )
