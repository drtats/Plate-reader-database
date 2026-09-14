from __future__ import annotations

import copy
import csv
import io
import json
from dataclasses import replace

import pytest

from plate_reader.application.contracts import AssayType, PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_tabular_export import (
    GROWTH_ADDITIONAL_LAYOUT_HEADERS,
    GROWTH_MATCHING_CONCENTRATION_HEADERS,
    GROWTH_MEASUREMENT_HEADERS,
    GROWTH_METADATA_HEADERS,
    GrowthTabularExportBundle,
    export_growth_tabular_data,
)
from plate_reader.application.services.growth_workflow import GrowthRunView
from plate_reader.domain.common import DomainValidationError
from plate_reader.domain.growth.cultivation import DEFAULT_CULTIVATION_PATTERN


def test_multi_run_export_preserves_raw_background_and_corrected_od_contract() -> None:
    bundle = export_growth_tabular_data((_view(),))

    assert bundle.measurements.filename == "experiment_1_dbea359c.csv"
    assert bundle.metadata.filename == "experiment_1_dbea359c_metadata.csv"
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
    assert rows[0]["Raw label"] == "raw-a1"
    assert rows[0]["Display name"] == "sample-a1"
    assert rows[0]["Background group"] == "plate"
    assert rows[0]["Plot"] == "True"
    assert rows[0]["Group"] == "sample"
    assert rows[0]["Inoculum size"] == "0.0005"
    assert rows[0]["Inoculum unit"] == "OD600"
    assert rows[0]["Treatment"] == "Mecillinam"
    assert rows[0]["Concentration"] == "3.0"
    assert rows[0]["Concentration unit"] == "ug/mL"
    assert rows[0]["T0 added (min)"] == "0.0"

    metadata_rows = list(
        csv.DictReader(io.StringIO(bundle.metadata.content.decode("utf-8"), newline=""))
    )
    assert tuple(metadata_rows[0]) == GROWTH_METADATA_HEADERS
    assert bundle.metadata.row_count == len(metadata_rows) == 2
    assert metadata_rows[0]["Run ID"] == "dbea359c"
    assert metadata_rows[0]["Experiment Name"] == "Experiment 1"
    assert json.loads(metadata_rows[0]["Editable Metadata JSON"])["Culture_volume_uL"] == 200
    assert any("no cultivation ID" in warning for warning in bundle.warnings)


def test_measurement_export_contains_every_canonical_growth_layout_column() -> None:
    canonical_layout_columns = {
        "Well",
        "Raw label",
        "Display name",
        "Blank",
        "Background group",
        "Plot",
        "Group",
        "Media",
        "Strain",
        "Inoculum size",
        "Inoculum unit",
        "Replicate",
        "Notes",
        "Treatment",
        "Concentration",
        "Concentration unit",
        "T0 added (min)",
    }

    assert canonical_layout_columns <= set(GROWTH_MEASUREMENT_HEADERS)
    suffix = (
        *GROWTH_ADDITIONAL_LAYOUT_HEADERS,
        *GROWTH_MATCHING_CONCENTRATION_HEADERS,
        "Experiment Date",
    )
    assert GROWTH_MEASUREMENT_HEADERS[-len(suffix) :] == suffix


def test_single_run_filename_matches_reference_experiment_name_and_hash_pattern() -> None:
    view = _view()
    view.snapshot.metadata["name"] = "250910_MG_BW_Mec_RDM_Growth_OD48h_10min_int_tats_mod"

    bundle = export_growth_tabular_data((view,))

    expected = "250910_mg_bw_mec_rdm_growth_od48h_10min_int_tats_mod_dbea359c"
    assert bundle.measurements.filename == f"{expected}.csv"
    assert bundle.metadata.filename == f"{expected}_metadata.csv"


def test_multi_run_export_keeps_generic_filenames() -> None:
    second = _view()
    second = replace(
        second,
        snapshot=replace(
            second.snapshot,
            plate_id=PlateId("plate-2"),
            metadata={**second.snapshot.metadata, "legacy_run_id": "abcdef12"},
        ),
    )

    bundle = export_growth_tabular_data((_view(), second))

    assert bundle.measurements.filename == "growth_runs.csv"
    assert bundle.metadata.filename == "growth_runs_metadata.csv"


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


def test_custom_layout_columns_are_preserved_in_both_exports() -> None:
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
    assert tuple(metadata_rows[0]) == (*GROWTH_METADATA_HEADERS, "Oxygen", "Vessel")
    assert measurement_rows[0]["Oxygen"] == "anaerobic"
    assert measurement_rows[0]["Vessel"] == ""
    assert metadata_rows[0]["Oxygen"] == "anaerobic"
    assert metadata_rows[0]["Vessel"] == ""


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
        "run_id": "dbea359c",
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
            "inoculum_unit": "OD600",
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
                "legacy_run_id": "dbea359c",
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


def _registry_view() -> GrowthRunView:
    view = _view()
    plate_custom = json.loads(str(view.snapshot.metadata["plate_custom_json"]))
    plate_custom["cultivation_registry"] = {
        "Team_Code": "PN",
        "CultivationSystemCode": "MP96A",
        "Objective": "Test combination treatments",
        "ProgramMetric": "CD2",
        "CultivationExperiment": "PN-EXP-MG1655-MP96A[023-054]",
        "CultivationProtocol": "Protocol v1.1",
        "SampleAnalysisProtocol": "NA",
        "InoculationDateTime": "2025-09-09 15:00:00",
    }
    view.snapshot.metadata["plate_custom_json"] = json.dumps(plate_custom)
    for index, well in enumerate(view.snapshot.wells, 1):
        well["strain"] = "MG1655"
        custom = json.loads(str(well["custom_json"]))
        custom.update(
            {
                "Cultivation": f"PN-EXP-MG1655-MP96A023R{index}",
                "Team_Code": "PN",
                "CultivationSystemCode": "MP96A",
                "CultivationRun": "023",
                "Strain/Strain_Aliases": "K12",
                "treatment_2": "Na-sulfadiazine",
                "conc_2": 1000,
                "unit_2": "mg/L",
            }
        )
        well["custom_json"] = json.dumps(custom)
    return view


def test_cultivation_metadata_links_every_observation_and_preserves_separate_values() -> None:
    view = _registry_view()
    bundle = export_growth_tabular_data((view,))
    data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    by_id = {row["Cultivation"]: row for row in metadata}
    assert len(by_id) == 2
    assert not bundle.warnings
    assert len(data[0]) == len(GROWTH_MEASUREMENT_HEADERS)
    assert len(metadata[0]) == len(GROWTH_METADATA_HEADERS)
    for row in data:
        assert row["Cultivation ID"] in by_id
        assert row["Strain"] == by_id[row["Cultivation ID"]]["Strain"] == "MG1655"
        assert "Cultivation_Registry_Link/Condition" not in row
        assert row["Raw OD"] and row["Background Mean OD"] and row["Background Subtracted OD"]
        assert row["Treatment 2"] == "Na-sulfadiazine"
        assert row["Concentration 2"] == "1000"
        assert row["Concentration unit 2"] == "mg/L"
    assert float(data[0]["Culture_Age_h"]) == pytest.approx(12.2 / 60)
    assert metadata[0]["Local_Cultivation_ID"] == "Experiment 1 A01"
    assert metadata[0]["Vessel_Alphabetical_ID"] == "A"
    assert metadata[0]["Vessel_Numeric_ID"] == "1"
    assert metadata[0]["Objective"] == "Test combination treatments"
    assert metadata[0]["Strain/Strain_Aliases"] == "K12"
    assert metadata[0]["CultivationRun"] == "023"
    assert metadata[0]["Treatment 2"] == "Na-sulfadiazine"
    assert (
        json.loads(metadata[0]["Plate Metadata JSON"])["cultivation_registry"]["ProgramMetric"]
        == "CD2"
    )


def test_pattern_ids_export_join_with_per_well_strains_and_persisted_numbers() -> None:
    from plate_reader.domain.growth.cultivation import DEFAULT_CULTIVATION_PATTERN

    view = _registry_view()
    for index, well in enumerate(view.snapshot.wells, 1):
        strain = "MG1655" if index == 1 else "11_J3"
        well["strain"] = strain
        custom = json.loads(str(well["custom_json"]))
        custom.update(
            {
                "Cultivation": f"PN-EXP-{strain}-001-A0{index}-R{index}",
                "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
                "CultivationExperimentCode": "001",
                "CultivationRun": "",
            }
        )
        well["custom_json"] = json.dumps(custom)
    shared = json.loads(str(view.snapshot.metadata["plate_custom_json"]))
    shared["cultivation_registry"]["CultivationExperimentCode"] = "099"
    shared["cultivation_registry"]["CultivationIDPattern"] = "{well}"
    view.snapshot.metadata["plate_custom_json"] = json.dumps(shared)
    bundle = export_growth_tabular_data((view,))
    data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    by_id = {row["Cultivation"]: row for row in metadata}
    assert set(by_id) == {"PN-EXP-MG1655-001-A01-R1", "PN-EXP-11_J3-001-A02-R2"}
    for row in data:
        saved = by_id[row["Cultivation ID"]]
        assert row["Strain"] == saved["Strain"]
        assert row["Cultivation experiment code"] == saved["CultivationExperimentCode"] == "001"
        assert (
            row["Cultivation ID pattern"]
            == saved["CultivationIDPattern"]
            == DEFAULT_CULTIVATION_PATTERN
        )
        assert row["Raw OD"] and row["Background Subtracted OD"]
    view.snapshot.wells[0]["replicate"] = 2
    with pytest.raises(ValueError, match="no longer matches"):
        export_growth_tabular_data((view,))


def test_condition_replicate_exports_global_number_and_keeps_local_label() -> None:
    from plate_reader.domain.growth.cultivation import DEFAULT_CULTIVATION_PATTERN
    from plate_reader.domain.growth.cultivation_conditions import cultivation_condition_key

    view = _registry_view()
    well = view.snapshot.wells[0]
    custom = json.loads(str(well["custom_json"]))
    custom.update(
        {
            "Cultivation": "PN-EXP-MG1655-001-A01-R3",
            "CultivationExperimentCode": "001",
            "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
            "CultivationReplicate": 3,
            "CultivationReplicateScope": "study-a",
            "CultivationConditionFields": ["oxygen"],
            "CultivationReplicateMode": "condition",
        }
    )
    well["custom_json"] = json.dumps(custom)
    custom["CultivationConditionKey"] = cultivation_condition_key(
        {**well, "plate_id": str(view.snapshot.plate_id)},
        view.snapshot.metadata,
        "study-a",
        ("oxygen",),
    )
    well["custom_json"] = json.dumps(custom)
    # The local label can change or be absent without changing the saved global R.
    for local in (99, None):
        well["replicate"] = local
        bundle = export_growth_tabular_data((view,))
        data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
        metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
        assert data[0]["Cultivation ID"] == "PN-EXP-MG1655-001-A01-R3"
        assert data[0]["Cultivation replicate"] == metadata[0]["Replicate"] == "3"
        assert (
            data[0]["Local replicate"]
            == metadata[0]["LocalReplicate"]
            == ("" if local is None else str(local))
        )
        assert metadata[0]["CultivationConditionFields"] == '["oxygen"]'
        assert data[0]["Cultivation replicate scope"] == "study-a"
    well["concentration"] = 99
    with pytest.raises(ValueError, match="conditions changed"):
        export_growth_tabular_data((view,))


def _selection_view(plate_id: str, experiment_date: str, code: str) -> GrowthRunView:
    view = copy.deepcopy(_view())
    view.snapshot.metadata["experiment_date"] = experiment_date
    view.snapshot.metadata["created_at"] = f"{experiment_date}T09:00:00Z"
    view.snapshot.metadata["legacy_run_id"] = plate_id
    plate_custom = json.loads(str(view.snapshot.metadata["plate_custom_json"]))
    plate_custom["cultivation_registry"] = {
        "Team_Code": "PN",
        "CultivationExperimentCode": code,
        "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
    }
    view.snapshot.metadata["plate_custom_json"] = json.dumps(plate_custom)
    for well in view.snapshot.wells:
        well["well_id"] = f"{plate_id}-{well['well_id']}"
    for observation in view.snapshot.raw_observations:
        observation["well_id"] = f"{plate_id}-{observation['well_id']}"
    return replace(view, snapshot=replace(view.snapshot, plate_id=PlateId(plate_id)))


def _csv_rows(
    bundle: GrowthTabularExportBundle,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        list(csv.DictReader(io.StringIO(bundle.measurements.content.decode()))),
        list(csv.DictReader(io.StringIO(bundle.metadata.content.decode()))),
    )


def test_selection_export_assigns_r_within_only_selected_runs_and_preserves_saved_values() -> None:
    early = _selection_view("early", "2026-08-01", "001")
    late = _selection_view("late", "2026-09-01", "002")
    late_a1 = late.snapshot.wells[0]
    saved_custom = json.loads(str(late_a1["custom_json"]))
    saved_custom.update(
        {
            "Cultivation": "PN-EXP-NCM3722-002-A01-R9",
            "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
            "CultivationExperimentCode": "002",
            "CultivationReplicate": 9,
        }
    )
    late_a1["custom_json"] = json.dumps(saved_custom)
    originals = (repr(early.snapshot), repr(late.snapshot))

    pair = export_growth_tabular_data(
        (late, early), assign_selected_replicates=True, condition_fields=("oxygen",)
    )
    reversed_pair = export_growth_tabular_data(
        (early, late), assign_selected_replicates=True, condition_fields=("oxygen",)
    )
    single = export_growth_tabular_data((late,), assign_selected_replicates=True)
    pair_data, pair_meta = _csv_rows(pair)
    reverse_data, reverse_meta = _csv_rows(reversed_pair)
    single_data, single_meta = _csv_rows(single)
    expected = {
        "early": "PN-EXP-NCM3722-001-A01-R1",
        "late": "PN-EXP-NCM3722-002-A01-R1",
    }
    by_run = {row["Run ID"]: row for row in pair_meta if row["Well"] == "A1"}
    assert {run_id: row["Cultivation"] for run_id, row in by_run.items()} == expected
    assert {
        row["Run ID"]: row["Cultivation"] for row in reverse_meta if row["Well"] == "A1"
    } == expected
    assert next(row for row in single_meta if row["Well"] == "A1")["Cultivation"] == (
        "PN-EXP-NCM3722-002-A01-R1"
    )
    assert by_run["late"]["SavedCultivation"] == "PN-EXP-NCM3722-002-A01-R9"
    assert by_run["late"]["LocalReplicate"] == "1"
    assert by_run["late"]["Replicate"] == by_run["late"]["CultivationReplicate"] == "1"
    assert by_run["late"]["CultivationReplicateMode"] == "export_run"
    assert by_run["late"]["CultivationConditionFields"] == '["oxygen"]'
    assert pair.effective_condition_fields == ("oxygen",)
    assert len(pair.replicate_preview) == 2
    assert {row["Cultivation ID"] for row in pair.replicate_preview} == set(expected.values())
    for rows, metadata in (
        (pair_data, pair_meta),
        (reverse_data, reverse_meta),
        (single_data, single_meta),
    ):
        by_id = {row["Cultivation"]: row for row in metadata if row["Cultivation"]}
        for row in rows:
            if row["Well"] == "A1":
                assert row["Cultivation ID"] in by_id
                assert row["Replicate"] == by_id[row["Cultivation ID"]]["Replicate"]
                assert row["Local replicate"] == "1"
                assert row["Raw OD"] in {"0.088", "0.1"}
    assert (repr(early.snapshot), repr(late.snapshot)) == originals


def test_selection_export_resets_r_for_distinct_condition_or_scope() -> None:
    early = _selection_view("early", "2026-08-01", "001")
    late = _selection_view("late", "2026-09-01", "002")
    late.snapshot.wells[0]["concentration"] = 4.0
    bundle = export_growth_tabular_data((early, late), assign_selected_replicates=True)
    _, metadata = _csv_rows(bundle)
    assert {row["Replicate"] for row in metadata if row["Well"] == "A1"} == {"1"}

    late.snapshot.wells[0]["concentration"] = None
    plate_custom = json.loads(str(late.snapshot.metadata["plate_custom_json"]))
    plate_custom["cultivation_registry"]["CultivationReplicateScope"] = "different"
    late.snapshot.metadata["plate_custom_json"] = json.dumps(plate_custom)
    scoped = export_growth_tabular_data((early, late), assign_selected_replicates=True)
    _, scoped_meta = _csv_rows(scoped)
    assert {row["Replicate"] for row in scoped_meta if row["Well"] == "A1"} == {"1"}


def test_selection_export_keeps_od_when_id_settings_missing_and_rejects_bad_pattern() -> None:
    view = _selection_view("only", "2026-08-01", "001")
    plate_custom = json.loads(str(view.snapshot.metadata["plate_custom_json"]))
    plate_custom["cultivation_registry"].pop("Team_Code")
    view.snapshot.metadata["plate_custom_json"] = json.dumps(plate_custom)
    bundle = export_growth_tabular_data((view,), assign_selected_replicates=True)
    data, metadata = _csv_rows(bundle)
    a1 = next(row for row in metadata if row["Well"] == "A1")
    assert a1["Cultivation"] == ""
    assert a1["CultivationReplicate"] == "1"
    assert any("missing team" in warning for warning in bundle.warnings)
    assert any(row["Raw OD"] for row in data if row["Well"] == "A1")

    plate_custom["cultivation_registry"]["Team_Code"] = "PN"
    plate_custom["cultivation_registry"]["CultivationIDPattern"] = "{team}-{well}"
    view.snapshot.metadata["plate_custom_json"] = json.dumps(plate_custom)
    with pytest.raises(DomainValidationError, match=r"include.*replicate"):
        export_growth_tabular_data((view,), assign_selected_replicates=True)


def test_selection_export_requires_strain_even_when_custom_pattern_omits_it() -> None:
    view = _selection_view("only", "2026-08-01", "001")
    plate_custom = json.loads(str(view.snapshot.metadata["plate_custom_json"]))
    plate_custom["cultivation_registry"]["CultivationIDPattern"] = "{team}-{well}-R{replicate}"
    view.snapshot.metadata["plate_custom_json"] = json.dumps(plate_custom)
    view.snapshot.wells[0]["strain"] = None

    bundle = export_growth_tabular_data((view,), assign_selected_replicates=True)
    data, metadata = _csv_rows(bundle)
    a1 = next(row for row in metadata if row["Well"] == "A1")
    assert a1["Cultivation"] == ""
    assert a1["CultivationReplicate"] == "1"
    assert a1["CultivationReplicateMode"] == "export_run"
    assert any("missing strain" in warning for warning in bundle.warnings)
    assert any(row["Raw OD"] for row in data if row["Well"] == "A1")


def test_registry_export_rejects_duplicates_across_runs_and_changed_layout_identity() -> None:
    view = _registry_view()
    second = replace(
        _registry_view(), snapshot=replace(_registry_view().snapshot, plate_id=PlateId("other"))
    )
    with pytest.raises(ValueError, match="Duplicate cultivation ID"):
        export_growth_tabular_data((view, second))
    view.snapshot.wells[0]["strain"] = "11_J3"
    with pytest.raises(ValueError, match="no longer matches"):
        export_growth_tabular_data((view,))


def test_registry_export_does_not_invent_missing_ids_or_experiment_descriptions() -> None:
    bundle = export_growth_tabular_data((_view(),))
    data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert all(row["Cultivation ID"] == "" for row in data)
    assert all(
        row["Cultivation"] == row["Objective"] == row["CultivationExperiment"] == ""
        for row in metadata
    )
    assert any("1 wells have no cultivation ID" in warning for warning in bundle.warnings)
    assert data[0]["Culture_Age_h"] == "2.0"


def test_per_well_inoculation_overrides_shared_date_and_bad_dates_fail() -> None:
    view = _registry_view()
    custom = json.loads(str(view.snapshot.wells[0]["custom_json"]))
    custom["InoculationDateTime"] = "2025-09-09 15:12:12"
    view.snapshot.wells[0]["custom_json"] = json.dumps(custom)
    bundle = export_growth_tabular_data((view,))
    data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert data[0]["Culture_Age_h"] == "0.0"
    assert float(data[1]["Culture_Age_h"]) == pytest.approx(1 / 6)
    custom["InoculationDateTime"] = "invalid"
    view.snapshot.wells[0]["custom_json"] = json.dumps(custom)
    with pytest.raises(ValueError, match="valid date and time"):
        export_growth_tabular_data((view,))


def test_export_generator_works_without_saved_ids_and_normalizes_units() -> None:
    from plate_reader.application.services.growth_tabular_export import ExportCultivationSettings

    views = tuple(_selection_view(f"plate-{i}", f"2026-08-{i + 1:02d}", "") for i in range(4))
    for view, unit in zip(views, ("ug/mL", "µg/mL", "μg/mL", "Œºg/mL"), strict=True):
        view.snapshot.metadata["plate_custom_json"] = "{}"
        well = view.snapshot.wells[0]
        well["concentration_unit"] = unit
        well["concentration"] = 2.0
        well["inoculum_unit"] = unit.replace("g/mL", "L")
        custom = json.loads(str(well["custom_json"]))
        custom.update({"unit_2": unit, "unit_3": unit, "conc_2": 3.0, "conc_3": 4.0})
        well["custom_json"] = json.dumps(custom)
    originals = repr(views)
    bundle = export_growth_tabular_data(
        tuple(reversed(views)),
        assign_selected_replicates=True,
        cultivation_settings=ExportCultivationSettings(team_code="PN"),
    )
    data, metadata = _csv_rows(bundle)
    for i, view in enumerate(views, 1):
        run = str(view.snapshot.plate_id)
        meta = next(row for row in metadata if row["Run ID"] == run and row["Well"] == "A1")
        assert meta["Cultivation"] == f"PN-EXP-NCM3722-{i:03d}-A01-R1"
        assert meta["Replicate"] == meta["CultivationReplicate"] == "1"
        assert meta["LocalReplicate"] == "1"
        assert meta["SavedCultivation"] == ""
        assert json.loads(meta["Well Metadata JSON"])["unit_2"] in (
            "ug/mL",
            "µg/mL",
            "μg/mL",
            "Œºg/mL",
        )
        for row in (meta, *(row for row in data if row["Run ID"] == run and row["Well"] == "A1")):
            assert (
                row["Concentration unit"]
                == row["Concentration unit 2"]
                == row["Concentration unit 3"]
                == "ug/mL"
            )
            assert row["Replicate"] == "1"
        for row in data:
            if row["Run ID"] == run and row["Well"] == "A1":
                assert row["Cultivation ID"] == meta["Cultivation"]
                assert row["Local replicate"] == "1"
                assert row["Inoculum unit"] == "uL"
                assert "ug/mL" in row["Condition 1 State"]
                assert "ug/mL" in row["Condition 2 State"]
                assert "ug/mL" in row["Condition 3 State"]
                assert row["Raw OD"] in {"0.088", "0.1"}
    assert repr(views) == originals


def test_export_generator_can_override_pattern_team_and_system_without_saving() -> None:
    from plate_reader.application.services.growth_tabular_export import ExportCultivationSettings
    from plate_reader.domain.growth.cultivation import LEGACY_CULTIVATION_PATTERN

    view = _selection_view("only", "2026-08-01", "007")
    original = repr(view)
    bundle = export_growth_tabular_data(
        (view,),
        assign_selected_replicates=True,
        cultivation_settings=ExportCultivationSettings(
            pattern=LEGACY_CULTIVATION_PATTERN, team_code="AB", system_code="BRV"
        ),
    )
    data, metadata = _csv_rows(bundle)
    assert data[0]["Cultivation ID"] == "AB-EXP-NCM3722-BRV007R1"
    assert metadata[0]["CultivationRun"] == "007"
    assert repr(view) == original
    for pattern in ("{team}-{well}", "{team}-{well}-{replicate}", "{team.__class__}-R{replicate}"):
        with pytest.raises(DomainValidationError):
            export_growth_tabular_data(
                (view,),
                assign_selected_replicates=True,
                cultivation_settings=ExportCultivationSettings(pattern=pattern),
            )
    with pytest.raises(DomainValidationError, match="requires selected-run"):
        export_growth_tabular_data((view,), cultivation_settings=ExportCultivationSettings())


@pytest.mark.parametrize("slot", [1, 2, 3])
def test_rounded_dilutions_export_matching_doses_with_independent_run_replicates(slot: int) -> None:
    early = _selection_view("early", "2026-08-01", "001")
    late = _selection_view("late", "2026-09-01", "002")
    for view, dose in ((early, 0.1875), (late, 0.19)):
        well = view.snapshot.wells[0]
        if slot == 1:
            well["concentration"] = dose
        else:
            custom = json.loads(str(well["custom_json"]))
            custom.update(
                {f"treatment_{slot}": "Drug", f"conc_{slot}": dose, f"unit_{slot}": "ug/mL"}
            )
            well["custom_json"] = json.dumps(custom)
    before = repr((early, late))
    rounded = export_growth_tabular_data(
        (late, early),
        assign_selected_replicates=True,
        concentration_significant_figures=2,
    )
    exact = export_growth_tabular_data((late, early), assign_selected_replicates=True)
    data, metadata = _csv_rows(rounded)
    _, exact_meta = _csv_rows(exact)
    assert {row["Replicate"] for row in exact_meta if row["Well"] == "A1"} == {"1"}
    suffix = "" if slot == 1 else f" {slot}"
    for rows in (data, metadata):
        for row in rows:
            if row["Well"] != "A1":
                continue
            assert row["Replicate"] == "1"
            assert row["Concentration" + suffix] == (
                "0.1875" if row["Run ID"] == "early" else "0.19"
            )
            assert row["Matching concentration" + suffix] == "0.19"
            assert row["Concentration matching significant figures"] == "2"
    assert (
        "0.1875"
        in next(row for row in rounded.replicate_preview if row["Run ID"] == "early")[
            "Entered concentrations"
        ]
    )
    assert all("0.19" in row["Matching concentrations"] for row in rounded.replicate_preview)
    assert all(
        row["Concentration matching"] == "2 significant figures"
        for row in rounded.replicate_preview
    )
    for row in exact_meta:
        if row["Well"] == "A1":
            assert row["Concentration matching significant figures"] == "exact"
            assert row["Matching concentration" + suffix] == row["Concentration" + suffix]
    assert repr((early, late)) == before


def test_rounding_setting_rejects_saved_id_mode_and_invalid_precision() -> None:
    for precision in (0, -1, 13, True, 2.5):
        with pytest.raises(DomainValidationError):
            export_growth_tabular_data(
                (_view(),),
                assign_selected_replicates=True,
                concentration_significant_figures=precision,
            )
    with pytest.raises(DomainValidationError, match="requires selected-run"):
        export_growth_tabular_data((_view(),), concentration_significant_figures=2)


def test_each_run_restarts_replicates_and_rounding_still_groups_wells_within_a_run() -> None:
    views = tuple(_selection_view(f"plate-{i}", "2026-08-01", f"{i + 1:03d}") for i in range(2))
    for view in views:
        first, second = view.snapshot.wells
        second_id = second["well_id"]
        second.clear()
        second.update(copy.deepcopy(first))
        second.update(
            {
                "well_id": second_id,
                "position": "A2",
                "concentration": 0.19,
                "concentration_unit": "µg/mL",
            }
        )
        first.update({"concentration": 0.1875, "concentration_unit": "ug/mL"})
    before = repr(views)
    pair = export_growth_tabular_data(
        views, assign_selected_replicates=True, concentration_significant_figures=2
    )
    single = export_growth_tabular_data(
        (views[1],), assign_selected_replicates=True, concentration_significant_figures=2
    )
    exact = export_growth_tabular_data(views, assign_selected_replicates=True)
    data, metadata = _csv_rows(pair)
    assert {(row["Run ID"], row["Well"]): row["Replicate"] for row in metadata} == {
        ("plate-0", "A1"): "1",
        ("plate-0", "A2"): "2",
        ("plate-1", "A1"): "1",
        ("plate-1", "A2"): "2",
    }
    meta_by_id = {row["Cultivation"]: row for row in metadata}
    for row in data:
        assert row["Replicate"] == meta_by_id[row["Cultivation ID"]]["Replicate"]
        assert row["Matching concentration"] == "0.19"
    assert {row["Replicate"] for row in _csv_rows(exact)[1]} == {"1"}
    assert _csv_rows(single)[1] == [row for row in metadata if row["Run ID"] == "plate-1"]
    assert {row["Matching wells"] for row in pair.replicate_preview} == {2}
    assert repr(views) == before


def test_saved_workflow_rejects_unsaved_ids_without_mutating_input() -> None:
    view = _view()
    before = copy.deepcopy(view)
    with pytest.raises(ValueError, match="Preview alone does not save IDs") as error:
        export_growth_tabular_data((view,), require_saved_cultivation_ids=True)
    assert "Experiment 1" in str(error.value)
    assert view == before


def test_missing_clock_time_keeps_experiment_date_and_ignores_blank_id_warnings() -> None:
    view = _view()
    view.snapshot.metadata["plate_custom_json"] = "{}"
    view.snapshot.metadata["experiment_custom_json"] = "{}"
    bundle = export_growth_tabular_data((view,))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert all(row["Date Time"] == "" for row in rows)
    assert all(row["Experiment Date"] == "2025-09-09" for row in rows)
    assert all(row["Raw OD"] for row in rows)
    assert metadata[0]["Local_Cultivation_ID"] == "Experiment 1 A01"
    missing = [warning for warning in bundle.warnings if "no cultivation ID" in warning]
    assert len(missing) == 1
    assert "(A1)" in missing[0] and "A2" not in missing[0]
    assert "Experiment 1:" in missing[0]
    assert all(None not in row for row in rows + metadata)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"Start Date Time": "2025-09-09T15:12:12+00:00"}, "2025-09-09T15:12:12+00:00"),
        ({"Date": "09/09/25", "Time": "15:12:12"}, "2025-09-09T15:12:12"),
        ({"Date": "09/09/2025", "Time": "15:12:12"}, "2025-09-09T15:12:12"),
        ({"Date": "2025-09-09", "Time": "3:12:12 PM"}, "2025-09-09T15:12:12"),
        (
            {"Start Date Time": "invalid", "Date": "2025-09-09", "Time": "15:12:12"},
            "2025-09-09T15:12:12",
        ),
        ({"Date": "2025-09-09"}, ""),
        ({"Date": "bad date", "Time": "bad time"}, ""),
    ],
)
def test_source_timestamp_formats_and_incomplete_clock_are_preserved(
    source: dict[str, str], expected: str
) -> None:
    view = _view()
    view.snapshot.metadata["plate_custom_json"] = "{}"
    view.snapshot.metadata["experiment_custom_json"] = json.dumps(source)
    bundle = export_growth_tabular_data((view,))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert rows[0]["Date Time"] == expected
    assert rows[0]["Experiment Date"] == "2025-09-09"
    assert rows[0]["Raw OD"] == "0.088"


@pytest.mark.parametrize("metadata", [None, "", "{broken", "[]", ["unstructured"]])
def test_unusable_optional_metadata_never_shifts_or_drops_measurements(metadata: object) -> None:
    view = _view()
    view.snapshot.metadata["plate_custom_json"] = metadata
    view.snapshot.metadata["experiment_custom_json"] = metadata
    view.snapshot.wells[0]["custom_json"] = metadata
    bundle = export_growth_tabular_data((view,))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert len(rows) == 3
    assert rows[0]["Raw OD"] == "0.088"
    assert all(None not in row for row in rows)
    assert all(row["Date Time"] == "" for row in rows)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("well_id", "unknown", "unknown well"),
        ("well_id", "", "cannot be empty"),
        ("channel", "", "cannot be empty"),
        ("time_index", True, "must be an integer"),
        ("elapsed_microseconds", "60000000", "must be an integer"),
    ],
)
def test_invalid_observation_identity_or_time_rejects_whole_export(
    field: str, value: object, message: str
) -> None:
    view = _view()
    view.snapshot.raw_observations[0][field] = value
    with pytest.raises(ValueError, match=message):
        export_growth_tabular_data((view,))


@pytest.mark.parametrize("value", [True, "not a number", "NaN", "Infinity", None])
def test_unusable_od_does_not_become_a_fabricated_corrected_value(value: object) -> None:
    view = _view()
    for observation in view.snapshot.raw_observations:
        observation["value_raw"] = value
    before = copy.deepcopy(view)
    bundle = export_growth_tabular_data((view,))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert len(rows) == 3
    assert all(row["Raw OD"] == row["Background Subtracted OD"] == "" for row in rows)
    assert view == before


def test_stale_background_reports_remedy_and_preserves_raw_rows() -> None:
    base = _view()
    view = GrowthRunView(base.snapshot, (), (), True)
    bundle = export_growth_tabular_data((view,))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert len(rows) == 3 and rows[0]["Raw OD"] == "0.088"
    assert all(row["Background Subtracted OD"] == "" for row in rows)
    assert all(row["Background QC Reason"] == "stale_background_revision" for row in rows)
    assert any("Compute a current background revision" in warning for warning in bundle.warnings)


def test_structured_custom_metadata_with_commas_and_newlines_round_trips() -> None:
    view = _view()
    custom = {"Extra object": {"name": "x,y\nnext", "value": 1}, "Extra list": ["a,b", "c\nd"]}
    view.snapshot.wells[0]["custom_json"] = json.dumps(custom)
    bundle = export_growth_tabular_data((view,), custom_columns=("Unused",))
    for artifact in (bundle.measurements, bundle.metadata):
        rows = list(csv.DictReader(io.StringIO(artifact.content.decode())))
        first = next(row for row in rows if row["Well"] == "A1")
        assert json.loads(first["Extra object"]) == custom["Extra object"]
        assert json.loads(first["Extra list"]) == custom["Extra list"]
        assert first["Unused"] == ""
        assert all(None not in row for row in rows)
