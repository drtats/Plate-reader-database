from __future__ import annotations

import pytest

from plate_reader.application.services import (
    GrowthHeatmapCell,
    GrowthHeatmapData,
    GrowthHeatmapTimepoint,
)
from plate_reader.ui.plotting import growth_heatmap_figure


def test_heatmap_figure_uses_physical_cells_and_explicit_identity() -> None:
    timepoint = GrowthHeatmapTimepoint(2, 1_200_000_000)
    data = GrowthHeatmapData(
        (
            GrowthHeatmapCell(
                "A1", "control", 0, 0, "od600", 2, 1_200_000_000, 0.2, 0.3, 0.1, True
            ),
            GrowthHeatmapCell("H12", "last", 7, 11, "od600", 2, 1_200_000_000, 0.5, 0.6, 0.1, True),
        ),
        (),
        "od600",
        timepoint,
        True,
    )

    figure = growth_heatmap_figure.__wrapped__(
        data, "raw-hash", "revision-1", "od600", 2, 1_200_000_000
    )

    assert figure.layout.title.text == "Growth heatmap · od600 · 20 min · Background corrected"
    assert figure.data[0].z[0][0] == 0.2
    assert figure.data[0].z[7][11] == 0.5
    assert tuple(figure.data[0].customdata[0][0]) == (
        "A1",
        "control",
        0.3,
        0.1,
        "corrected",
    )
    with pytest.raises(ValueError, match="cache identity"):
        growth_heatmap_figure.__wrapped__(data, "raw-hash", "revision-1", "other", 2, 1_200_000_000)
