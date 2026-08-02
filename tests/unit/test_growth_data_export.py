from __future__ import annotations

import csv
import io

import pytest

from plate_reader.application.services import (
    GrowthDataExportContext,
    GrowthPlotData,
    GrowthPlotPoint,
    export_growth_plot_data_csv,
    export_growth_plot_wide_csv,
)
from plate_reader.application.services.growth_plot_styles import (
    GrowthPlotColorMode,
    GrowthPlotColorOptions,
    GrowthPlotStyles,
    GrowthSeriesStyle,
)


def test_selected_plot_csv_preserves_points_metadata_and_excel_quoting() -> None:
    plotted = GrowthPlotData(
        (
            GrowthPlotPoint(
                "A2",
                "strain-a",
                10.0,
                "od600",
                0.123456789012345,
                0.223456789012345,
                0.1,
                True,
                1,
                600_000_000,
            ),
            GrowthPlotPoint(
                "A2",
                "strain-a",
                10.0,
                "gfp",
                2.5,
                2.5,
                None,
                False,
                1,
                600_000_000,
            ),
        ),
        (),
        True,
    )
    wells = (
        {
            "position": "A1",
            "display_name": "not selected",
            "custom_json": '{"Batch":"unused"}',
        },
        {
            "position": "A2",
            "display_name": "sample, two",
            "grouping_label": "group-a",
            "medium": "M9",
            "strain": "strain-a",
            "inoculum_size": 0.05,
            "inoculum_unit": "OD600",
            "replicate": 2,
            "treatment": "drug",
            "concentration": 1.25,
            "concentration_unit": "ug/mL",
            "notes": "line one\nline two",
            "custom_json": '{"Batch":"batch,1","Oxygen":"low"}',
        },
    )

    artifact = export_growth_plot_data_csv(
        plotted,
        wells,
        GrowthDataExportContext("plate-1", "Experiment, one", "Plate 1", "revision-7"),
        "My Plot / 24 h",
    )
    rows = list(csv.DictReader(io.StringIO(artifact.content.decode("utf-8-sig"), newline="")))

    assert artifact.filename == "my-plot--24-h-data.csv"
    assert artifact.content.startswith(b"\xef\xbb\xbf")
    assert artifact.row_count == len(plotted.points) == 2
    assert {row["Well"] for row in rows} == {"A2"}
    assert [row["Channel"] for row in rows] == ["od600", "gfp"]
    assert rows[0]["Experiment name"] == "Experiment, one"
    assert rows[0]["Display name"] == "sample, two"
    assert rows[0]["Notes"] == "line one\nline two"
    assert rows[0]["Custom: Batch"] == "batch,1"
    assert rows[0]["Custom: Oxygen"] == "low"
    assert rows[0]["Background revision"] == "revision-7"
    assert int(rows[0]["Time index"]) == plotted.points[0].time_index
    assert int(rows[0]["Elapsed microseconds"]) == plotted.points[0].elapsed_microseconds
    assert float(rows[0]["Raw value"]) == plotted.points[0].value_raw
    assert float(rows[0]["Plotted value"]) == plotted.points[0].value
    assert rows[0]["Correction applied"] == "True"
    assert rows[1]["Background mean"] == ""
    assert rows[1]["Correction applied"] == "False"


def test_plot_csv_handles_empty_selection_and_rejects_mismatched_layout() -> None:
    context = GrowthDataExportContext("plate-1", "Experiment", "Plate", "raw")
    empty = export_growth_plot_data_csv(
        GrowthPlotData((), (), False),
        ({"position": "A1", "custom_json": "{}"},),
        context,
        "",
    )

    assert empty.filename == "growth-plot-plate-1-data.csv"
    assert empty.row_count == 0
    assert len(empty.content.decode("utf-8-sig").splitlines()) == 1
    with pytest.raises(ValueError, match="outside the supplied layout"):
        export_growth_plot_data_csv(
            GrowthPlotData(
                (GrowthPlotPoint("A2", "A2", 0, "od600", 1, 1, None, False),),
                (),
                False,
            ),
            ({"position": "A1", "custom_json": "{}"},),
            context,
            "plot",
        )
    with pytest.raises(ValueError, match="revision identity"):
        GrowthDataExportContext("plate-1", "Experiment", "Plate", "")


def test_wide_plot_csv_has_time_then_one_column_per_visible_series() -> None:
    plotted = GrowthPlotData(
        (
            GrowthPlotPoint("A1", "control", 0.0, "od600", 0.1, 0.1, None, False),
            GrowthPlotPoint("A2", "sample", 0.0, "od600", 0.2, 0.2, None, False),
            GrowthPlotPoint("A1", "control", 10.0, "od600", 0.3, 0.3, None, False),
            GrowthPlotPoint("A2", "sample", 10.0, "od600", 0.4, 0.4, None, False),
        ),
        (),
        False,
    )
    styles = GrowthPlotStyles(
        (
            GrowthSeriesStyle("A1", "od600", "control", "#000000", "A1"),
            GrowthSeriesStyle("A2", "od600", "sample", "#ffffff", "A2"),
        ),
        GrowthPlotColorOptions(GrowthPlotColorMode.RAINBOW_PLATE_ORDER),
    )

    artifact = export_growth_plot_wide_csv(plotted, styles, "My Growth Plot")
    rows = list(csv.reader(io.StringIO(artifact.content.decode("utf-8-sig"))))

    assert artifact.filename == "my-growth-plot-wide.csv"
    assert artifact.row_count == 2
    assert rows == [
        ["Time (minutes)", "control", "sample"],
        ["0.0", "0.1", "0.2"],
        ["10.0", "0.3", "0.4"],
    ]
