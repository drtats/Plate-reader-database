from __future__ import annotations

import csv
import io
import json

import pytest

from plate_reader.application.contracts import AssayType, PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_tabular_export import (
    GROWTH_MEASUREMENT_HEADERS,
    GROWTH_METADATA_HEADERS,
    export_growth_tabular_data,
)
from plate_reader.application.services.growth_workflow import GrowthRunView


def test_multi_run_export_preserves_raw_background_and_corrected_od_contract() -> None:
    bundle = export_growth_tabular_data((_view(),))

    assert bundle.measurements.filename == "growth_runs.csv"
    assert bundle.metadata.filename == "growth_runs_metadata.csv"
    assert not bundle.measurements.content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in bundle.measurements.content
    assert bundle.measurements.content.endswith(b"\n")
    rows = list(
        csv.DictReader(io.StringIO(bundle.measurements.content.decode("utf-8"), newline=""))
    )
    assert tuple(rows[0]) == GROWTH_MEASUREMENT_HEADERS
    assert bundle.measurements.row_count == len(rows) == 3
    assert [(row["Well"], row["Time Min"]) for row in rows] == [
        ("A1", "0.0"),
        ("A1", "10.0"),
        ("A2", "0.0"),
    ]
    assert rows[0]["Raw OD"] == "0.088"
    assert rows[0]["Background Mean OD"] == "0.088625"
    assert rows[0]["Background Subtracted OD"] == "0.0001"
    assert rows[1]["Raw OD"] == "0.1"
    assert rows[1]["Background Mean OD"] == "0.09"
    assert float(rows[1]["Background Subtracted OD"]) == pytest.approx(0.01)
    assert rows[0]["Background SD OD"] == "0.001"
    assert rows[0]["Background Blank N"] == "4"
    assert rows[0]["Background QC Flag"] == "False"
    assert rows[0]["Background QC Reason"] == ""
    assert rows[1]["Background QC Flag"] == "True"
    assert rows[1]["Background QC Reason"] == "high_cv"
    assert rows[0]["Date Time"] == "2025-09-09T15:12:12"
    assert rows[1]["Date Time"] == "2025-09-09T15:22:12"
    assert float(rows[1]["Culture Age H"]) == pytest.approx(2 + 10 / 60)
    assert rows[0]["Condition 1 State"] == "Mecillinam 3.0 ug/mL"
    assert rows[0]["Microplate ID"] == "Plate 58"

    metadata_rows = list(
        csv.DictReader(io.StringIO(bundle.metadata.content.decode("utf-8"), newline=""))
    )
    assert tuple(metadata_rows[0]) == GROWTH_METADATA_HEADERS
    assert bundle.metadata.row_count == len(metadata_rows) == 1
    assert metadata_rows[0]["Run ID"] == "legacy-run-1"
    assert metadata_rows[0]["Experiment Name"] == "Experiment 1"
    assert json.loads(metadata_rows[0]["Editable Metadata JSON"])["Culture_volume_uL"] == 200
    assert not bundle.warnings


def test_missing_background_keeps_raw_od_and_exposes_qc_reason() -> None:
    base = _view()
    view = GrowthRunView(base.snapshot, (), (), False)

    bundle = export_growth_tabular_data((view,))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))

    assert rows[0]["Raw OD"] == "0.088"
    assert rows[0]["Background Mean OD"] == ""
    assert rows[0]["Background Subtracted OD"] == ""
    assert rows[0]["Background QC Flag"] == "True"
    assert rows[0]["Background QC Reason"] == "missing_background_revision"
    assert any("no current background revision" in warning for warning in bundle.warnings)


def test_custom_layout_columns_are_appended_only_to_observation_export() -> None:
    view = _view()
    view.snapshot.wells[0]["custom_json"] = json.dumps(
        {
            "treatment_1": "Mecillinam",
            "conc_1": 3.0,
            "unit_1": "ug/mL",
            "t0_added_min": 0.0,
            "oxygen": "anaerobic",
        }
    )

    bundle = export_growth_tabular_data((view,), custom_columns=("Oxygen", "Vessel"))
    measurement_rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata_rows = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))

    assert tuple(measurement_rows[0]) == (*GROWTH_MEASUREMENT_HEADERS, "Oxygen", "Vessel")
    assert tuple(metadata_rows[0]) == GROWTH_METADATA_HEADERS
    assert measurement_rows[0]["Oxygen"] == "anaerobic"
    assert measurement_rows[0]["Vessel"] == ""
    assert "Oxygen" not in metadata_rows[0]


def test_export_rejects_empty_duplicate_and_non_growth_views() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        export_growth_tabular_data(())
    with pytest.raises(ValueError, match="unique plate IDs"):
        export_growth_tabular_data((_view(), _view()))
    view = _view()
    view.snapshot.metadata["assay_type"] = AssayType.MIC
    with pytest.raises(ValueError, match="not a growth run"):
        export_growth_tabular_data((view,))


def _view() -> GrowthRunView:
    editable = {
        "Culture_Age_hours": 2.0,
        "Culture_volume_uL": 200,
        "Microplate_ID": "",
    }
    source = {
        "Date": "9/9/2025",
        "Time": "3:12:12 PM",
        "Reader Type": "Synergy H1",
        "Plate Number": "Plate 58",
    }
    legacy = {
        "run_id": "legacy-run-1",
        "editable_metadata_json": json.dumps(editable),
        "source_metadata_json": json.dumps(source),
    }
    wells = (
        {
            "well_id": "well-a1",
            "position": "A1",
            "display_name": "sample-a1",
            "raw_label": "raw-a1",
            "is_blank": False,
            "background_group": "plate",
            "plot_selected": True,
            "notes": "note",
            "custom_json": json.dumps(
                {
                    "treatment_1": "Mecillinam",
                    "conc_1": 3.0,
                    "unit_1": "ug/mL",
                    "t0_added_min": 0.0,
                }
            ),
            "condition_custom_json": "{}",
            "strain": "NCM3722",
            "medium": "RDM",
            "replicate": 1,
            "inoculum_size": 0.0005,
            "grouping_label": "sample",
            "treatment": None,
            "concentration": None,
            "concentration_unit": None,
        },
        {
            "well_id": "well-a2",
            "position": "A2",
            "display_name": "sample-a2",
            "raw_label": None,
            "is_blank": True,
            "background_group": "plate",
            "plot_selected": False,
            "notes": None,
            "custom_json": "{}",
            "condition_custom_json": "{}",
            "strain": None,
            "medium": "RDM",
            "replicate": 2,
            "inoculum_size": None,
            "grouping_label": None,
            "treatment": None,
            "concentration": None,
            "concentration_unit": None,
        },
    )
    observations = (
        {
            "well_id": "well-a2",
            "channel": "od600",
            "time_index": 0,
            "elapsed_microseconds": 0,
            "value_raw": 0.2,
        },
        {
            "well_id": "well-a1",
            "channel": "od600",
            "time_index": 1,
            "elapsed_microseconds": 600_000_000,
            "value_raw": 0.1,
        },
        {
            "well_id": "well-a1",
            "channel": "od600",
            "time_index": 0,
            "elapsed_microseconds": 0,
            "value_raw": 0.088,
        },
    )
    backgrounds = (
        {
            "background_group": "plate",
            "channel": "od600",
            "time_index": 0,
            "elapsed_microseconds": 0,
            "mean_value": 0.088625,
            "std_value": 0.001,
            "coefficient_of_variation": 0.01,
            "blank_count": 4,
            "qc_status": "good",
        },
        {
            "background_group": "plate",
            "channel": "od600",
            "time_index": 1,
            "elapsed_microseconds": 600_000_000,
            "mean_value": 0.09,
            "std_value": 0.02,
            "coefficient_of_variation": 0.2,
            "blank_count": 4,
            "qc_status": "high_cv",
        },
    )
    return GrowthRunView(
        PlateSnapshot(
            PlateId("plate-1"),
            {
                "assay_type": AssayType.GROWTH,
                "name": "Experiment 1",
                "plate_name": "Plate 1",
                "legacy_run_id": "legacy-run-1",
                "project": "SMS",
                "experiment_date": "2025-09-09",
                "operator_name": "Researcher",
                "instrument": None,
                "temperature": 37.0,
                "experiment_custom_json": "{}",
                "plate_custom_json": json.dumps({"legacy_plate_meta": legacy}),
            },
            wells,
            observations,
            (),
        ),
        backgrounds,
        (),
        False,
    )
