from __future__ import annotations

import pytest

from plate_reader.application.services import (
    GrowthPlotColorMode,
    GrowthPlotColorOptions,
    GrowthPlotData,
    GrowthPlotPoint,
    GrowthPlotStyles,
    GrowthSeriesStyle,
)
from plate_reader.ui.plotting import (
    GrowthPlotOptions,
    growth_curve_figure,
    growth_plate_overview_figure,
    plot_download_config,
)


def test_growth_figure_applies_limits_symlog_labels_and_title() -> None:
    plot_data = GrowthPlotData(
        (
            GrowthPlotPoint("A1", "control", 0.0, "od600", -0.01, 0.09, 0.1, True),
            GrowthPlotPoint("A1", "control", 10.0, "od600", 0.2, 0.3, 0.1, True),
        ),
        (),
        True,
    )
    options = GrowthPlotOptions(
        title="Growth comparison",
        x_max=60,
        y_min=-0.1,
        y_max=1.0,
        symlog=True,
    )

    figure = growth_curve_figure.__wrapped__(plot_data, options, "raw-hash", "revision")

    assert figure.layout.title.text == "Growth comparison"
    assert tuple(figure.layout.xaxis.range) == (0, 60)
    assert figure.layout.yaxis.title.text == "OD (symmetric log)"
    assert figure.data[0].name == "control"
    assert list(figure.data[0].customdata[0]) == [-0.01, "od600", "corrected"]


def test_growth_figure_supports_linear_scale_and_empty_selection() -> None:
    empty = GrowthPlotData((), (), False)
    options = GrowthPlotOptions(x_max=30, y_min=0, y_max=2, symlog=False)

    empty_figure = growth_curve_figure.__wrapped__(empty, options, "raw", "revision")

    assert empty_figure.layout.title.text == "No wells selected"
    with pytest.raises(ValueError, match="X maximum"):
        GrowthPlotOptions(x_max=0)
    with pytest.raises(ValueError, match="Y minimum"):
        GrowthPlotOptions(y_min=1, y_max=1)


def test_growth_figure_uses_shared_styles_and_keeps_channels_separate() -> None:
    data = GrowthPlotData(
        (
            GrowthPlotPoint("A1", "sample", 0.0, "od600", 0.1, 0.1, None, False),
            GrowthPlotPoint("A1", "sample", 0.0, "gfp", 1.0, 1.0, None, False),
            GrowthPlotPoint("A1", "sample", 10.0, "od600", 0.2, 0.2, None, False),
            GrowthPlotPoint("A1", "sample", 10.0, "gfp", 2.0, 2.0, None, False),
        ),
        (),
        False,
    )
    styles = GrowthPlotStyles(
        (
            GrowthSeriesStyle("A1", "gfp", "sample (A1) · gfp", "#ff0000", "gfp"),
            GrowthSeriesStyle("A1", "od600", "sample (A1) · od600", "#00ff00", "od"),
        ),
        GrowthPlotColorOptions(GrowthPlotColorMode.CATEGORICAL, "channel"),
    )

    figure = growth_curve_figure.__wrapped__(
        data,
        GrowthPlotOptions(x_max=10, y_min=0, y_max=3, symlog=False),
        "raw",
        "revision",
        styles,
    )

    assert [trace.name for trace in figure.data] == [
        "sample (A1) · gfp",
        "sample (A1) · od600",
    ]
    assert [trace.line.color for trace in figure.data] == ["#ff0000", "#00ff00"]
    assert [list(trace.y) for trace in figure.data] == [[1.0, 2.0], [0.1, 0.2]]


def test_plot_download_uses_safe_stable_png_filename() -> None:
    config = plot_download_config("My Growth: 24 h / test", "plate-id")

    assert config["displaylogo"] is False
    assert config["toImageButtonOptions"] == {
        "format": "png",
        "filename": "my-growth-24-h--test",
        "width": 1_200,
        "height": 750,
        "scale": 2,
    }


def test_growth_plate_overview_uses_all_physical_subplots_and_cached_inputs() -> None:
    plot_data = GrowthPlotData(
        (
            GrowthPlotPoint("H12", "last", 10.0, "od600", 0.4, 0.5, 0.1, True),
            GrowthPlotPoint("A1", "first", 10.0, "od600", 0.2, 0.3, 0.1, True),
            GrowthPlotPoint("A1", "first", 0.0, "od600", 0.1, 0.2, 0.1, True),
        ),
        (),
        True,
    )

    figure = growth_plate_overview_figure.__wrapped__(plot_data, "raw-hash", "revision")

    assert figure.layout.title.text == "96-well growth curves (background-corrected)"
    assert len(figure.layout.annotations) == 96
    assert len(figure.data) == 2
    assert list(figure.data[0].x) == [0.0, 10.0]
    assert figure.data[0].type == "scattergl"
    assert figure.data[1].xaxis == "x96"


def test_plot_download_accepts_overview_dimensions() -> None:
    config = plot_download_config("overview", "plate", width=1_800, height=1_200)
    options = config["toImageButtonOptions"]

    assert isinstance(options, dict)
    assert options["width"] == 1_800
    assert options["height"] == 1_200
