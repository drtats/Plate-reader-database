"""Persistent plate-condition identities are stable across library edits."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy

import pytest

from plate_reader.domain.common import DomainValidationError
from plate_reader.domain.growth.cultivation_conditions import (
    normalize_cultivation_condition_fields,
)
from plate_reader.domain.growth.cultivation_registry import (
    PLATE_CONDITION_PATTERN,
    SCHEME,
    RegistryPlatePlan,
    RegistrySettings,
    plan_plate_condition_cultivations,
    saved_plate_condition_identity,
)


def _row(plate: str, position: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "plate_id": plate,
        "well_id": f"uuid:{plate}:{position}",
        "position": position,
        "created_at": f"2026-01-{int(plate[1:]):02d}T01:00:00",
        "experiment_date": f"2026-01-{int(plate[1:]):02d}",
        "deleted_at": None,
        "is_blank": 0,
        "strain": "MG1655",
        "medium": "M9",
        "replicate": 7,
        "treatment": "Drug",
        "concentration": "0.10",
        "concentration_unit": "µM",
        "inoculum_size": "0.1",
        "inoculum_unit": "OD600",
        "temperature": 37,
        "temperature_unit": "C",
        "custom_json": "{}",
        "condition_custom_json": "{}",
        "plate_custom_json": '{"cultivation_registry":{"description":"Keep me"}}',
        "experiment_custom_json": "{}",
    }
    row.update(changes)
    return row


SETTINGS = RegistrySettings("ST", "MP96A")


def _plan(rows: list[dict[str, object]], *plates: str) -> tuple[RegistryPlatePlan, ...]:
    return plan_plate_condition_cultivations(rows, plates, settings=SETTINGS)


def _persist(rows: list[dict[str, object]], plans: tuple[RegistryPlatePlan, ...]) -> None:
    for plan in plans:
        for row in rows:
            if row["plate_id"] != plan.plate_id:
                continue
            plate_custom = json.loads(str(row["plate_custom_json"]))
            plate_custom["cultivation_registry"] = plan.registry
            row["plate_custom_json"] = json.dumps(plate_custom)
            assignment = next(a for a in plan.assignments if a["Well"] == row["position"])
            custom = json.loads(str(row["custom_json"]))
            custom.update({key: value for key, value in assignment.items() if key != "Well"})
            row["custom_json"] = json.dumps(custom)


def test_well_order_grouping_range_internal_only_and_multistrain() -> None:
    rows = [
        _row("p1", "B1", concentration="1", strain="MG1655"),
        _row("p1", "A3", concentration="2", strain="K12"),
        _row("p1", "A2", concentration="0.100", concentration_unit="uM"),
        _row("p1", "A1", concentration="0.104"),
        _row("p1", "H12", is_blank=1, strain=""),
        _row("p1", "H11", strain=""),
    ]
    before = deepcopy(rows)
    (plan,) = _plan(rows, "p1")
    assert rows == before
    assert [a["Well"] for a in plan.assignments] == ["A1", "A2", "A3", "B1", "H11", "H12"]
    a1, a2, a3, b1, missing, blank = plan.assignments
    assert a1["Cultivation"] == "ST-EXP-MG1655-MP96A0101R1"
    assert a2["Cultivation"] == "ST-EXP-MG1655-MP96A0101R2"
    assert a3["Cultivation"] == "ST-EXP-K12-MP96A0102R1"
    assert b1["Cultivation"] == "ST-EXP-MG1655-MP96A0103R1"
    assert a1["CultivationExperiment"] == "ST-EXP-MG1655-MP96A[0101,0103]"
    assert a3["CultivationExperiment"] == "ST-EXP-K12-MP96A[0102]"
    assert a1["CultivationReplicate"] == a1["TechnicalReplicate"] == 1
    assert a1["BiologicalReplicateGroup"] == "0101"
    assert a1["LocalReplicate"] == 7
    assert a1["CultivationNumberingScheme"] == SCHEME
    assert a1["CultivationIDPattern"] == PLATE_CONDITION_PATTERN
    assert missing["InternalCultivationID"] == "uuid:p1:H11"
    assert missing["Local_Cultivation_ID"] == "EXP01-H11"
    assert blank["CultivationPlateNumber"] == "01"
    assert "Cultivation" not in blank and "Cultivation" not in missing
    assert len(plan.warnings) == 1 and "1 wells missing strain" in plan.warnings[0]
    assert plan.registry["description"] == "Keep me"


def test_contiguous_range_and_chronology_reserve_deleted_and_over_99() -> None:
    rows = [_row(f"p{i}", "A1") for i in range(1, 101)]
    rows[0]["deleted_at"] = "2026-02-01"
    # Dates outside January are parsed as unknown and sorted by plate ID.
    for i, row in enumerate(rows, 1):
        row["experiment_date"] = f"2026-{(i - 1) // 28 + 1:02d}-{(i - 1) % 28 + 1:02d}"
        row["created_at"] = row["experiment_date"]
    (plan,) = _plan(rows, "p100")
    assert plan.plate_number == "100"
    assert plan.assignments[0]["CultivationRun"] == "10001"
    contiguous = [_row("p1", "A1", concentration=str(i)) for i in (1, 2, 3)]
    for i, row in enumerate(contiguous):
        row["position"] = f"A{i + 1}"
        row["well_id"] = f"uuid:{i}"
    (plan,) = _plan(contiguous, "p1")
    assert plan.assignments[0]["CultivationExperiment"] == "ST-EXP-MG1655-MP96A[0101-0103]"


def test_reexport_reuses_identity_and_new_wells_append_groups_and_replicates() -> None:
    rows = [_row("p1", "A1"), _row("p1", "A2", concentration="2")]
    (first,) = _plan(rows, "p1")
    _persist(rows, (first,))
    assert _plan(rows, "p1")[0].assignments == first.assignments
    rows.extend([_row("p1", "A3"), _row("p1", "A4", concentration="3")])
    (next_plan,) = _plan(rows, "p1")
    assert [a["CultivationConditionNumber"] for a in next_plan.assignments] == [
        "01",
        "02",
        "01",
        "03",
    ]
    assert [a["CultivationReplicate"] for a in next_plan.assignments] == [1, 1, 2, 1]
    _persist(rows, (next_plan,))
    original = deepcopy(rows)
    rows[0]["replicate"] = 93
    assert (
        saved_plate_condition_identity(rows[0], rows[0])["Cultivation"]
        == first.assignments[0]["Cultivation"]
    )
    assert _plan(rows, "p1")[0].assignments[0]["LocalReplicate"] == 7
    rows[:] = original


def test_saved_changed_conditions_or_rules_are_rejected() -> None:
    rows = [_row("p1", "A1")]
    _persist(rows, _plan(rows, "p1"))
    rows[0]["concentration"] = "3"
    with pytest.raises(DomainValidationError, match="fingerprint"):
        _plan(rows, "p1")
    rows[0]["concentration"] = "0.10"
    with pytest.raises(DomainValidationError, match="rules"):
        plan_plate_condition_cultivations(
            rows, ("p1",), settings=RegistrySettings("ST", "MP96A", 3)
        )
    with pytest.raises(DomainValidationError, match="team/system"):
        plan_plate_condition_cultivations(rows, ("p1",), settings=RegistrySettings("NEW", "MP96A"))


@pytest.mark.parametrize(
    "field", ["Cultivation", "CultivationRun", "InternalCultivationID", "TechnicalReplicate"]
)
def test_saved_validator_rejects_inconsistent_parts(field: str) -> None:
    rows = [_row("p1", "A1")]
    _persist(rows, _plan(rows, "p1"))
    custom = json.loads(str(rows[0]["custom_json"]))
    custom[field] = "wrong"
    rows[0]["custom_json"] = json.dumps(custom)
    with pytest.raises(DomainValidationError, match="inconsistent"):
        saved_plate_condition_identity(rows[0], rows[0])


def test_reject_conflicting_saved_reservations_and_blank_transition() -> None:
    rows = [_row("p1", "A1"), _row("p2", "A1")]
    plans = _plan(rows, "p1", "p2")
    _persist(rows, plans)
    plate_custom = json.loads(str(rows[1]["plate_custom_json"]))
    plate_custom["cultivation_registry"]["CultivationPlateNumber"] = "01"
    rows[1]["plate_custom_json"] = json.dumps(plate_custom)
    with pytest.raises(DomainValidationError):
        _plan(rows, "p1")
    _persist(rows, (plans[1],))
    rows[0]["is_blank"] = 1
    with pytest.raises(DomainValidationError, match="blank"):
        _plan(rows, "p1")


def test_legacy_replacement_tracks_history_without_changing_input() -> None:
    rows = [_row("p1", "A1", custom_json='{"Cultivation":"OLD","LocalReplicate":"old"}')]
    (plan,) = _plan(rows, "p1")
    assert plan.assignments[0]["PreviousCultivationIDs"] == ["OLD"]
    assert plan.assignments[0]["LocalReplicate"] == "old"
    assert rows[0]["custom_json"] == '{"Cultivation":"OLD","LocalReplicate":"old"}'


def test_all_blank_without_codes_still_gets_internal_and_local_ids() -> None:
    rows = [_row("p1", "A1", strain="", is_blank=1)]
    (plan,) = plan_plate_condition_cultivations(rows, ("p1",), settings=RegistrySettings())
    assert plan.assignments == (
        {
            "Well": "A1",
            "InternalCultivationID": "uuid:p1:A1",
            "Local_Cultivation_ID": "EXP01-A01",
            "CultivationPlateNumber": "01",
        },
    )
    assert "Team_Code" not in plan.registry and "CultivationSystemCode" not in plan.registry


def test_subselection_keeps_plate_numbers_and_inactive_group_reservation() -> None:
    rows = [_row("p1", "A1"), _row("p2", "A1"), _row("p2", "A2", concentration="2")]
    _persist(rows, _plan(rows, "p2"))
    rows[1]["deleted_at"] = rows[2]["deleted_at"] = "2026-03-01"
    rows[1]["concentration"] = "999"  # inactive historical row still reserves 02
    rows.append(_row("p3", "A3", concentration="3"))
    (plan,) = _plan(rows, "p3")
    assert plan.plate_number == "03"
    assert _plan(rows, "p1")[0].plate_number == "01"
    with pytest.raises(DomainValidationError, match="Deleted"):
        _plan(rows, "p2")


def test_legacy_id_on_another_well_blocks_new_id_collision() -> None:
    rows = [
        _row("p1", "A1"),
        _row("p2", "A1", custom_json='{"Cultivation":"ST-EXP-MG1655-MP96A0101R1"}'),
    ]
    with pytest.raises(DomainValidationError, match="collides"):
        _plan(rows, "p1")


def test_missing_strain_clears_old_external_and_preserves_history() -> None:
    rows = [_row("p1", "A1", strain="", custom_json='{"Cultivation":"OLD"}')]
    (plan,) = _plan(rows, "p1")
    assert plan.assignments[0]["Cultivation"] == ""
    assert plan.assignments[0]["PreviousCultivationIDs"] == ["OLD"]


def test_ninety_six_unique_conditions_use_all_group_slots() -> None:
    rows = [
        _row("p1", f"{chr(ord('A') + row)}{column}", concentration=str(row * 12 + column))
        for row in range(8)
        for column in range(1, 13)
    ]
    (plan,) = _plan(rows, "p1")
    assert len(plan.assignments) == 96
    assert plan.assignments[0]["CultivationConditionNumber"] == "01"
    assert plan.assignments[-1]["CultivationConditionNumber"] == "96"


@pytest.mark.parametrize(
    "field",
    [
        "InternalCultivationID",
        "Local_Cultivation_ID",
        "CultivationNumberingScheme",
        "CultivationPlateNumber",
        "CultivationConditionNumber",
        "TechnicalReplicate",
        "BiologicalReplicateGroup",
        "CultivationConcentrationSignificantFigures",
        "CultivationExperiment",
        "PreviousCultivationIDs",
    ],
)
@pytest.mark.parametrize("transform", [str, str.swapcase])
def test_generated_field_cannot_be_a_condition_even_on_blank_plate(
    field: str, transform: Callable[[str], str]
) -> None:
    rows = [_row("p1", "A1", strain="", is_blank=1)]
    original = deepcopy(rows)
    settings = RegistrySettings(condition_fields=(f" {transform(field)} ",))
    with pytest.raises(DomainValidationError, match="reserved"):
        plan_plate_condition_cultivations(rows, ("p1",), settings=settings)
    assert rows == original


def test_public_condition_field_normalization_matches_saved_key_rules() -> None:
    assert normalize_cultivation_condition_fields((" oxygen ", "pH", "oxygen", "")) == (
        "oxygen",
        "pH",
    )
    with pytest.raises(DomainValidationError, match="reserved"):
        normalize_cultivation_condition_fields(("tEcHnIcAlRePlIcAtE",))


def test_former_plate_local_label_keeps_cultivation_and_experiment_numbers() -> None:
    rows = [_row("p1", "A1")]
    (original,) = _plan(rows, "p1")
    _persist(rows, (original,))
    custom = json.loads(str(rows[0]["custom_json"]))
    custom["Local_Cultivation_ID"] = "P01-A01"
    rows[0]["custom_json"] = json.dumps(custom)
    identity = saved_plate_condition_identity(rows[0], rows[0])
    assert identity["Cultivation"] == original.assignments[0]["Cultivation"]
    (updated,) = _plan(rows, "p1")
    assert updated.plate_number == original.plate_number
    assert updated.assignments == original.assignments
    assert updated.assignments[0]["Local_Cultivation_ID"] == "EXP01-A01"


@pytest.mark.parametrize(
    ("original", "code"),
    [
        ("acrB MG", "acrB_MG"),
        ("acrB  MG", "acrB_MG"),
        ("ΔacrB MG", "dacrB_MG"),
        ("δacrB MG", "dacrB_MG"),
        ("K-12 ΔacrB", "K_12_dacrB"),
        ("1-2", "1_2"),
        ("11_J3", "11_J3"),
    ],
)
def test_strain_code_normalization_survives_save_repreview_and_ranges(
    original: str, code: str
) -> None:
    rows = [_row("p1", "A1", strain=original), _row("p1", "A2", strain=original)]
    before = deepcopy(rows)
    (plan,) = _plan(rows, "p1")
    assert rows == before
    assert plan.assignments[0]["Cultivation"] == f"ST-EXP-{code}-MP96A0101R1"
    assert plan.assignments[1]["Cultivation"] == f"ST-EXP-{code}-MP96A0101R2"
    assert plan.assignments[0]["CultivationExperiment"] == f"ST-EXP-{code}-MP96A[0101]"
    if original != code:
        assert any(repr(original) in warning and repr(code) in warning for warning in plan.warnings)
    _persist(rows, (plan,))
    assert _plan(rows, "p1")[0].assignments == plan.assignments
    assert (
        saved_plate_condition_identity(rows[0], rows[0])["Cultivation"]
        == (plan.assignments[0]["Cultivation"])
    )
    assert rows[0]["strain"] == original
    # Strain-code normalization must not hide a change to scientific metadata.
    rows[0]["strain"] = original + " changed"
    with pytest.raises(DomainValidationError):
        _plan(rows, "p1")


def test_invalid_strain_error_identifies_experiment_well_and_label() -> None:
    with pytest.raises(DomainValidationError, match=r"experiment 01, well A1") as error:
        _plan([_row("p1", "A1", strain="acrB\x00MG")], "p1")
    assert "acrB" in str(error.value) and "nonprinting" in str(error.value)
