"""Prepare exact channel/timepoint Growth heatmap data without UI coupling."""

from __future__ import annotations

from dataclasses import dataclass

from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_plotting import PrepareGrowthPlotDataService
from plate_reader.domain.common import DomainIssue, WellPosition


@dataclass(frozen=True, slots=True)
class GrowthHeatmapTimepoint:
    time_index: int
    elapsed_microseconds: int

    @property
    def elapsed_minutes(self) -> float:
        return self.elapsed_microseconds / 60_000_000


@dataclass(frozen=True, slots=True)
class GrowthHeatmapChannel:
    channel: str
    timepoints: tuple[GrowthHeatmapTimepoint, ...]


@dataclass(frozen=True, slots=True)
class GrowthHeatmapCatalog:
    channels: tuple[GrowthHeatmapChannel, ...]

    def timepoints_for(self, channel: str) -> tuple[GrowthHeatmapTimepoint, ...]:
        for item in self.channels:
            if item.channel == channel:
                return item.timepoints
        raise ValueError(f"Unknown Growth heatmap channel: {channel}")


@dataclass(frozen=True, slots=True)
class GrowthHeatmapCell:
    position: str
    label: str
    row_index: int
    column_index: int
    channel: str
    time_index: int
    elapsed_microseconds: int
    value: float
    value_raw: float
    background_mean: float | None
    correction_applied: bool


@dataclass(frozen=True, slots=True)
class GrowthHeatmapData:
    cells: tuple[GrowthHeatmapCell, ...]
    issues: tuple[DomainIssue, ...]
    channel: str
    timepoint: GrowthHeatmapTimepoint
    correction_requested: bool


class BuildGrowthHeatmapCatalogService:
    """List exact stored channel/time identities available for heatmaps."""

    def execute(self, snapshot: PlateSnapshot) -> GrowthHeatmapCatalog:
        by_channel: dict[str, set[GrowthHeatmapTimepoint]] = {}
        for row in snapshot.raw_observations:
            channel = str(row.get("channel", "")).strip()
            if not channel:
                raise ValueError("Growth heatmap observation channel cannot be empty")
            by_channel.setdefault(channel, set()).add(
                GrowthHeatmapTimepoint(
                    _integer(row.get("time_index"), "time_index"),
                    _integer(row.get("elapsed_microseconds"), "elapsed_microseconds"),
                )
            )
        return GrowthHeatmapCatalog(
            tuple(
                GrowthHeatmapChannel(
                    channel,
                    tuple(
                        sorted(
                            timepoints,
                            key=lambda item: (item.time_index, item.elapsed_microseconds),
                        )
                    ),
                )
                for channel, timepoints in sorted(by_channel.items(), key=lambda item: item[0])
            )
        )


class PrepareGrowthHeatmapDataService:
    """Build one 8x12-compatible slice through the shared plot-data service."""

    def execute(
        self,
        snapshot: PlateSnapshot,
        backgrounds: tuple[dict[str, object], ...],
        channel: str,
        timepoint: GrowthHeatmapTimepoint,
        *,
        corrected: bool,
    ) -> GrowthHeatmapData:
        catalog = BuildGrowthHeatmapCatalogService().execute(snapshot)
        if timepoint not in catalog.timepoints_for(channel):
            raise ValueError("Growth heatmap timepoint is not stored for the selected channel")
        positions = tuple(str(well["position"]) for well in snapshot.wells)
        channel_snapshot = PlateSnapshot(
            snapshot.plate_id,
            snapshot.metadata,
            snapshot.wells,
            tuple(
                row for row in snapshot.raw_observations if str(row.get("channel", "")) == channel
            ),
            snapshot.revisions,
        )
        plot_data = PrepareGrowthPlotDataService().execute(
            channel_snapshot,
            backgrounds,
            positions,
            corrected=corrected,
        )
        cells = []
        seen: set[str] = set()
        for point in plot_data.points:
            if (
                point.channel != channel
                or point.time_index != timepoint.time_index
                or point.elapsed_microseconds != timepoint.elapsed_microseconds
            ):
                continue
            if point.position in seen:
                raise ValueError(f"Growth heatmap has duplicate observation for {point.position}")
            seen.add(point.position)
            position = WellPosition.parse(point.position)
            cells.append(
                GrowthHeatmapCell(
                    point.position,
                    point.label,
                    position.row_index,
                    position.column_index,
                    point.channel,
                    point.time_index,
                    point.elapsed_microseconds,
                    point.value,
                    point.value_raw,
                    point.background_mean,
                    point.correction_applied,
                )
            )
        cells.sort(key=lambda cell: (cell.row_index, cell.column_index))
        return GrowthHeatmapData(
            tuple(cells),
            plot_data.issues,
            channel,
            timepoint,
            plot_data.correction_requested,
        )


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Growth heatmap {field} must be an integer")
    return value
