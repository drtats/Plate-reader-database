"""Comparison downloads preserve selected identities and raw measurements."""

import csv
import io

import pytest

from plate_reader.application.services.growth_comparison import (
    GrowthComparisonPlate,
    GrowthComparisonPlotResult,
    GrowthComparisonWell,
)
from plate_reader.application.services.growth_comparison_export import export_growth_comparison_csv
from plate_reader.application.services.growth_plotting import GrowthPlotData, GrowthPlotPoint


def _point(identity: str, time: int, channel: str, value: float) -> GrowthPlotPoint:
    return GrowthPlotPoint(
        identity,
        "shared label",
        time / 60_000_000,
        channel,
        value,
        value,
        None,
        False,
        time_index=7,
        elapsed_microseconds=time,
    )


def test_csv_preserves_identities_metadata_channels_and_independent_time_grids() -> None:
    plates = (
        GrowthComparisonPlate(
            "plate-a",
            (
                GrowthComparisonWell(
                    "plate-a",
                    "well-a",
                    "A1",
                    display_name='Sample, "試料"\nline two',
                    concentration=0.125,
                    concentration_unit="µg/mL",
                    medium="Medium",
                    inoculum_size=5,
                    inoculum_unit="log CFU/mL",
                    replicate=2,
                    strain="Strain A",
                    treatment="Treatment",
                    grouping_label="Group",
                ),
                GrowthComparisonWell("plate-a", "unselected", "A2"),
            ),
            "Experiment",
            "Shared plate name",
        ),
        GrowthComparisonPlate(
            "plate-b",
            (GrowthComparisonWell("plate-b", "well-b", "A1", is_blank=True),),
            "Experiment",
            "Shared plate name",
        ),
    )
    points = (
        _point("plate-a:well-a", 60_000_001, "od600", 0.123456789012345),
        _point("plate-b:well-b", 90_000_003, "od600", -0.02),
        _point("plate-a:well-a", 60_000_001, "fluorescence", 42.5),
    )
    result = GrowthComparisonPlotResult(GrowthPlotData(points, (), False), "key", 2, 2)

    artifact = export_growth_comparison_csv(result, plates)
    rows = list(csv.DictReader(io.StringIO(artifact.content.decode("utf-8-sig"))))

    assert artifact.filename == "plate-comparison-data.csv"
    assert artifact.row_count == len(rows) == 3
    assert [row["Well ID"] for row in rows] == ["well-a", "well-b", "well-a"]
    for row, point in zip(rows, points, strict=True):
        assert f"{row['Plate ID']}:{row['Well ID']}" == point.position
        assert row["Well"] == "A1"
        assert row["Channel"] == point.channel
        assert int(row["Time index"]) == 7
        assert int(row["Elapsed microseconds"]) == point.elapsed_microseconds
        assert float(row["Elapsed minutes"]) == point.elapsed_minutes
        assert float(row["Raw value"]) == point.value_raw
    assert rows[0]["Display name"] == 'Sample, "試料"\nline two'
    assert rows[0]["Concentration unit"] == "µg/mL"
    assert rows[0]["Concentration"] == "0.125"
    assert rows[0]["Replicate"] == "2"
    assert rows[0]["Strain"] == "Strain A"
    assert rows[0]["Inoculum size"] == "5"
    assert rows[1]["Concentration"] == ""
    assert rows[1]["Is blank"] == "True"


def test_csv_rejects_points_without_matching_metadata() -> None:
    result = GrowthComparisonPlotResult(
        GrowthPlotData((_point("missing:well", 0, "od600", 0.1),), (), False),
        "key",
        2,
        2,
    )
    with pytest.raises(ValueError, match="outside the supplied metadata index"):
        export_growth_comparison_csv(result, ())


def test_empty_csv_retains_column_headers() -> None:
    result = GrowthComparisonPlotResult(GrowthPlotData((), (), False), "key", 2, 2)
    artifact = export_growth_comparison_csv(result, ())
    reader = csv.DictReader(io.StringIO(artifact.content.decode("utf-8-sig")))
    assert artifact.row_count == 0
    assert "Raw value" in (reader.fieldnames or ())
    assert list(reader) == []
