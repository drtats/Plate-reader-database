"""CSV serialization of the already loaded raw plate comparison."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from plate_reader.application.services.growth_comparison import (
    GrowthComparisonPlate,
    GrowthComparisonPlotResult,
)
from plate_reader.application.services.growth_data_export import GrowthDataCsvArtifact


def export_growth_comparison_csv(
    result: GrowthComparisonPlotResult,
    plates: Sequence[GrowthComparisonPlate],
) -> GrowthDataCsvArtifact:
    """Export one row per rendered observation, joined by stable plate/well identity.

    Reuse the comparison's metadata index and exact prepared points without reads,
    recalculation, aggregation, or interpolation between different time grids.
    """

    wells = {
        f"{plate.plate_id}:{well.well_id}": (plate, well)
        for plate in plates
        for well in plate.wells
    }
    if any(point.position not in wells for point in result.plot_data.points):
        raise ValueError("Comparison data contains a well outside the supplied metadata index")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "Plate ID",
            "Experiment name",
            "Plate name",
            "Well ID",
            "Well",
            "Display name",
            "Strain",
            "Treatment",
            "Concentration",
            "Concentration unit",
            "Medium",
            "Inoculum size",
            "Inoculum unit",
            "Replicate",
            "Group",
            "Is blank",
            "Channel",
            "Time index",
            "Elapsed microseconds",
            "Elapsed minutes",
            "Raw value",
        )
    )
    for point in result.plot_data.points:
        plate, well = wells[point.position]
        writer.writerow(
            (
                plate.plate_id,
                plate.experiment_name,
                plate.plate_name,
                well.well_id,
                well.position,
                well.display_name,
                well.strain,
                well.treatment,
                well.concentration,
                well.concentration_unit,
                well.medium,
                well.inoculum_size,
                well.inoculum_unit,
                well.replicate,
                well.grouping_label,
                well.is_blank,
                point.channel,
                point.time_index,
                point.elapsed_microseconds,
                point.elapsed_minutes,
                point.value_raw,
            )
        )
    return GrowthDataCsvArtifact(
        "plate-comparison-data.csv",
        stream.getvalue().encode("utf-8-sig"),
        len(result.plot_data.points),
    )
