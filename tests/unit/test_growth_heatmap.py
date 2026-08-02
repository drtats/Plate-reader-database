from __future__ import annotations

import pytest

from plate_reader.application.contracts import PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services import (
    BuildGrowthHeatmapCatalogService,
    GrowthHeatmapTimepoint,
    PrepareGrowthHeatmapDataService,
    PrepareGrowthPlotDataService,
)
from plate_reader.domain.common import IssueCode
from plate_reader.domain.common.plate import PLATE_96


def test_catalog_lists_exact_stored_channels_and_timepoints() -> None:
    catalog = BuildGrowthHeatmapCatalogService().execute(growth_snapshot())

    assert [item.channel for item in catalog.channels] == ["fluorescence", "od600"]
    assert catalog.timepoints_for("od600") == (
        GrowthHeatmapTimepoint(0, 0),
        GrowthHeatmapTimepoint(1, 600_000_000),
        GrowthHeatmapTimepoint(2, 1_200_000_000),
    )
    assert catalog.timepoints_for("od600")[-1].elapsed_minutes == 20.0
    with pytest.raises(ValueError, match="Unknown Growth heatmap channel"):
        catalog.timepoints_for("missing")


def test_raw_heatmap_selects_one_exact_channel_and_timepoint_in_physical_order() -> None:
    result = PrepareGrowthHeatmapDataService().execute(
        growth_snapshot(),
        backgrounds(),
        "od600",
        GrowthHeatmapTimepoint(1, 600_000_000),
        corrected=False,
    )

    assert [cell.position for cell in result.cells] == ["A1", "A2"]
    assert [cell.value for cell in result.cells] == [0.8, 0.7]
    assert all(cell.channel == "od600" for cell in result.cells)
    assert all(cell.time_index == 1 for cell in result.cells)
    assert result.correction_requested is False
    assert result.issues == ()


def test_corrected_heatmap_exactly_matches_shared_plot_preparation() -> None:
    snapshot = growth_snapshot()
    timepoint = GrowthHeatmapTimepoint(1, 600_000_000)
    heatmap = PrepareGrowthHeatmapDataService().execute(
        snapshot,
        backgrounds(),
        "od600",
        timepoint,
        corrected=True,
    )
    plot = PrepareGrowthPlotDataService().execute(
        snapshot,
        backgrounds(),
        ("A1", "A2"),
        corrected=True,
    )
    expected = {
        point.position: point.value
        for point in plot.points
        if point.channel == "od600"
        and point.time_index == timepoint.time_index
        and point.elapsed_microseconds == timepoint.elapsed_microseconds
    }

    assert {cell.position: cell.value for cell in heatmap.cells} == expected
    assert [cell.value for cell in heatmap.cells] == pytest.approx([0.6, 0.5])
    assert all(cell.background_mean == 0.2 for cell in heatmap.cells)
    assert all(cell.correction_applied for cell in heatmap.cells)


def test_final_timepoint_is_selected_exactly_without_nearest_time_matching() -> None:
    result = PrepareGrowthHeatmapDataService().execute(
        growth_snapshot(),
        backgrounds(),
        "od600",
        GrowthHeatmapTimepoint(2, 1_200_000_000),
        corrected=False,
    )

    assert [cell.value for cell in result.cells] == [1.0, 0.9]
    assert all(cell.time_index == 2 for cell in result.cells)
    assert all(cell.elapsed_microseconds == 1_200_000_000 for cell in result.cells)


def test_missing_background_uses_raw_fallback_and_shared_warning() -> None:
    result = PrepareGrowthHeatmapDataService().execute(
        growth_snapshot(),
        (),
        "od600",
        GrowthHeatmapTimepoint(0, 0),
        corrected=True,
    )

    assert [cell.value for cell in result.cells] == [0.3, 0.4]
    assert all(not cell.correction_applied for cell in result.cells)
    assert [issue.code for issue in result.issues] == [IssueCode.MISSING_BACKGROUND]


def test_heatmap_rejects_unstored_timepoints_and_duplicate_cells() -> None:
    service = PrepareGrowthHeatmapDataService()
    with pytest.raises(ValueError, match="not stored"):
        service.execute(
            growth_snapshot(),
            backgrounds(),
            "od600",
            GrowthHeatmapTimepoint(99, 99),
            corrected=False,
        )

    snapshot = growth_snapshot()
    duplicate = PlateSnapshot(
        snapshot.plate_id,
        snapshot.metadata,
        snapshot.wells,
        (*snapshot.raw_observations, snapshot.raw_observations[0]),
        snapshot.revisions,
    )
    with pytest.raises(ValueError, match="duplicate observation"):
        service.execute(
            duplicate,
            backgrounds(),
            "od600",
            GrowthHeatmapTimepoint(0, 0),
            corrected=False,
        )


def growth_snapshot() -> PlateSnapshot:
    wells = tuple(
        {
            "well_id": f"well-{position.label}",
            "position": position.label,
            "display_name": "sample A1" if position.label == "A1" else "",
            "raw_label": "",
            "is_blank": 0,
            "background_group": "plate",
        }
        for position in PLATE_96.positions()
    )
    observations = (
        observation("A1", 0, 0, "od600", 0.3),
        observation("A2", 0, 0, "od600", 0.4),
        observation("A1", 1, 600_000_000, "od600", 0.8),
        observation("A2", 1, 600_000_000, "od600", 0.7),
        observation("A1", 2, 1_200_000_000, "od600", 1.0),
        observation("A2", 2, 1_200_000_000, "od600", 0.9),
        observation("A1", 0, 0, "fluorescence", 100.0),
        observation("A2", 0, 0, "fluorescence", 110.0),
    )
    return PlateSnapshot(
        PlateId("growth-heatmap"),
        {"manual_subtraction": 0.0, "channel": "od600"},
        wells,
        observations,
        (),
    )


def observation(
    position: str, time_index: int, elapsed_microseconds: int, channel: str, value: float
) -> dict[str, object]:
    return {
        "well_id": f"well-{position}",
        "time_index": time_index,
        "elapsed_microseconds": elapsed_microseconds,
        "channel": channel,
        "value_raw": value,
    }


def backgrounds() -> tuple[dict[str, object], ...]:
    return (
        background("od600", 0, 0, 0.1),
        background("od600", 1, 600_000_000, 0.2),
        background("od600", 2, 1_200_000_000, 0.25),
        background("fluorescence", 0, 0, 10.0),
    )


def background(
    channel: str, time_index: int, elapsed_microseconds: int, mean: float
) -> dict[str, object]:
    return {
        "background_group": "plate",
        "channel": channel,
        "time_index": time_index,
        "elapsed_microseconds": elapsed_microseconds,
        "mean_value": mean,
        "std_value": 0.01,
        "coefficient_of_variation": 0.01,
        "blank_count": 2,
        "qc_status": "good",
    }
