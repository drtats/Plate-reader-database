"""Condition identity and library-wide cultivation replicate planning."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from plate_reader.application.services.growth_cultivation_replicates import (
    plan_condition_replicates,
    plan_export_condition_replicates,
)
from plate_reader.domain.common import DomainValidationError
from plate_reader.domain.growth.cultivation_conditions import (
    cultivation_condition_key,
    matching_concentration,
    primary_condition_value,
    validate_concentration_precision,
)


def _row(plate_id: str, position: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "plate_id": plate_id,
        "well_id": f"{plate_id}:{position}",
        "position": position,
        "row_index": ord(position[0]) - ord("A"),
        "column_index": int(position[1:]) - 1,
        "is_blank": 0,
        "strain": "K12",
        "medium": "M9",
        "replicate": 1,
        "treatment": "drug A",
        "concentration": 0.1,
        "concentration_unit": "mg/mL",
        "inoculum_size": 0.02,
        "inoculum_unit": "OD600",
        "temperature": 37,
        "temperature_unit": "C",
        "custom_json": "{}",
        "condition_custom_json": "{}",
        "plate_custom_json": json.dumps(
            {
                "cultivation_registry": {"CultivationReplicateScope": "project-a"},
                "editable_metadata_json": {"Culture_volume_uL": 200},
            }
        ),
        "experiment_custom_json": "{}",
        "experiment_date": "2026-01-01",
        "created_at": "2026-01-01T10:00:00",
        "deleted_at": None,
    }
    row.update(changes)
    return row


def _key(row: dict[str, object], scope: str = "project-a", fields: tuple[str, ...] = ()) -> str:
    return cultivation_condition_key(row, row, scope, fields)


def test_condition_identity_normalizes_numbers_and_reordered_treatments() -> None:
    first = _row(
        "p1",
        "A01",
        custom_json=json.dumps(
            {
                "treatment_2": "drug B",
                "conc_2": "2.0",
                "unit_2": "uM",
            }
        ),
        concentration="0.10",
    )
    second = _row(
        "p2",
        "H12",
        concentration="2.00",
        concentration_unit="uM",
        treatment="drug B",
        custom_json=json.dumps({"treatment_2": "drug A", "conc_2": 0.1, "unit_2": "mg/mL"}),
        experiment_date="2026-08-01",
        replicate=9,
    )
    assert _key(first) == _key(second)


def test_structured_first_treatment_overrides_stale_custom_slot() -> None:
    row = _row("p1", "A01", custom_json='{"treatment_1":"old","conc_1":99}')
    baseline = _row("p2", "A01")
    assert _key(row) == _key(baseline)


@pytest.mark.parametrize(
    ("structured", "custom_field"),
    [
        ("treatment", "treatment_1"),
        ("concentration", "conc_1"),
        ("concentration_unit", "unit_1"),
    ],
)
def test_cleared_structured_slot_one_does_not_resurrect_stale_custom_value(
    structured: str, custom_field: str
) -> None:
    cleared = _row("p1", "A01", **{structured: None})
    stale = deepcopy(cleared)
    stale["custom_json"] = json.dumps({custom_field: "old value"})
    assert _key(cleared) != _key(stale)  # unedited legacy null still uses its JSON value
    stale["condition_custom_json"] = json.dumps({"primary_condition_overrides": [structured]})
    assert _key(cleared) == _key(stale)


def test_legacy_null_primary_fields_use_custom_treatment_until_edited() -> None:
    legacy = _row(
        "p1",
        "A01",
        treatment=None,
        concentration=None,
        concentration_unit=None,
        custom_json='{"treatment_1":"drug A","conc_1":"0.10","unit_1":"mg/mL"}',
    )
    assert _key(legacy) == _key(_row("p2", "A01"))
    custom = json.loads(str(legacy["custom_json"]))
    assert primary_condition_value(legacy, custom, "concentration", "conc_1") == "0.10"
    legacy["condition_custom_json"] = json.dumps(
        {"primary_condition_overrides": ["treatment", "concentration", "concentration_unit"]}
    )
    assert primary_condition_value(legacy, custom, "concentration", "conc_1") is None
    assert _key(legacy) != _key(_row("p2", "A01"))


def test_first_treatment_falls_back_to_custom_only_when_structured_key_is_absent() -> None:
    legacy = _row(
        "p1",
        "A01",
        custom_json='{"treatment_1":"drug A","conc_1":"0.10","unit_1":"mg/mL"}',
    )
    for name in ("treatment", "concentration", "concentration_unit"):
        del legacy[name]
    assert _key(legacy) == _key(_row("p2", "A01"))


@pytest.mark.parametrize(
    "field",
    [
        "Cultivation",
        "CultivationReplicate",
        "CultivationConditionKey",
        "CultivationConditionFields",
        "CultivationReplicateScope",
        "CultivationReplicateMode",
        "CultivationIDPattern",
        "CultivationExperimentCode",
        "CultivationRun",
        "Team_Code",
        "CultivationSystemCode",
        "well_id",
        "plate_id",
        "Well",
        "position",
        "Replicate",
        "LocalReplicate",
        "experiment_date",
        "created_at",
        "primary_condition_overrides",
    ],
)
def test_identity_fields_cannot_be_declared_as_additional_conditions(field: str) -> None:
    row = _row("p1", "A01", custom_json=json.dumps({field: "arbitrary"}))
    with pytest.raises(DomainValidationError, match="reserved"):
        _key(row, fields=(f" {field.swapcase()} ",))


def test_planner_rejects_self_referential_additional_field() -> None:
    row = _row("p1", "A01", custom_json='{"CultivationConditionKey":"old"}')
    with pytest.raises(DomainValidationError, match="reserved"):
        plan_condition_replicates(
            [row],
            target_plate_id="p1",
            registry={"CultivationConditionFields": "CultivationConditionKey"},
        )


def test_nonfinite_numeric_values_are_rejected() -> None:
    for value in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"):
        with pytest.raises(DomainValidationError, match="finite"):
            _key(_row("p1", "A01", concentration=value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strain", "K12-mutant"),
        ("medium", "LB"),
        ("concentration", 0.2),
        ("concentration_unit", "uM"),
        ("inoculum_size", 0.03),
        ("inoculum_unit", "cells/mL"),
        ("temperature", 30),
        ("temperature_unit", "F"),
    ],
)
def test_condition_identity_distinguishes_scientific_fields(field: str, value: object) -> None:
    row = _row("p1", "A01")
    changed = deepcopy(row)
    changed[field] = value
    assert _key(row) != _key(changed)


def test_condition_identity_distinguishes_culture_volume_and_declared_custom_field() -> None:
    row = _row("p1", "A01", custom_json='{"oxygen":"aerobic"}')
    changed = _row("p2", "A01", custom_json='{"oxygen":"anaerobic"}')
    assert _key(row) == _key(changed)
    assert _key(row, fields=("oxygen",)) != _key(changed, fields=("oxygen",))
    plate_custom = json.loads(str(changed["plate_custom_json"]))
    plate_custom["editable_metadata_json"]["Culture_volume_uL"] = "250.0"
    changed["plate_custom_json"] = json.dumps(plate_custom)
    assert _key(row) != _key(changed)


def test_unknown_strain_or_medium_is_unique_to_physical_well() -> None:
    for missing in ("strain", "medium"):
        a = _row("p1", "A01", **{missing: None})
        b = _row("p2", "A01", **{missing: None})
        assert _key(a) != _key(b)


def test_planner_numbers_all_matching_wells_in_date_and_well_order() -> None:
    rows = [
        _row("late", "A02", experiment_date="2026-02-01", replicate=1),
        _row("early", "B01", experiment_date="2026-01-01", replicate=7),
        _row("late", "A01", experiment_date="2026-02-01", replicate=1),
        _row("early", "A01", experiment_date="2026-01-01", replicate=7),
    ]
    plan = plan_condition_replicates(
        rows, target_plate_id="late", registry={"CultivationReplicateScope": "project-a"}
    )
    assert {position: item.replicate for position, item in plan.items()} == {"A1": 3, "A2": 4}
    assert {(item.matching_wells, item.matching_plates) for item in plan.values()} == {(4, 2)}


def test_planner_preserves_saved_reservations_and_fills_smallest_gap() -> None:
    reserved = _row("old", "A01")
    reserved["custom_json"] = json.dumps(
        {
            "CultivationReplicateMode": "condition",
            "CultivationConditionKey": _key(reserved),
            "CultivationReplicateScope": "project-a",
            "CultivationReplicate": 2,
        }
    )
    target = _row("new", "A01", experiment_date="2026-02-01")
    plan = plan_condition_replicates(
        [target, reserved],
        target_plate_id="new",
        registry={"CultivationReplicateScope": "project-a"},
    )
    assert plan["A1"].replicate == 1
    assert plan["A1"].matching_wells == 2


def test_matching_saved_reservation_is_reused_even_if_earlier_well_is_added() -> None:
    saved = _row("target", "A01", experiment_date="2026-02-01")
    saved["custom_json"] = json.dumps(
        {
            "CultivationReplicateMode": "condition",
            "CultivationConditionKey": _key(saved),
            "CultivationReplicateScope": "project-a",
            "CultivationReplicate": 5,
        }
    )
    earlier = _row("earlier", "A01", experiment_date="2026-01-01")
    plan = plan_condition_replicates(
        [saved, earlier],
        target_plate_id="target",
        registry={"CultivationReplicateScope": "project-a"},
    )
    assert plan["A1"].replicate == 5


def test_partial_condition_mode_reservation_is_rejected() -> None:
    row = _row("target", "A01", custom_json='{"CultivationReplicateMode":"condition"}')
    with pytest.raises(DomainValidationError, match="incomplete"):
        plan_condition_replicates([row], target_plate_id="target", registry={})


def test_stale_saved_condition_keeps_old_reservation_but_gets_new_group_number() -> None:
    edited = _row("edited", "A01", medium="LB")
    before = _row("edited", "A01", medium="M9")
    edited["custom_json"] = json.dumps(
        {
            "CultivationReplicateMode": "condition",
            "CultivationConditionKey": _key(before),
            "CultivationReplicateScope": "project-a",
            "CultivationReplicate": 1,
        }
    )
    target = _row("target", "A01", experiment_date="2026-02-01")
    plan = plan_condition_replicates(
        [edited, target],
        target_plate_id="target",
        registry={"CultivationReplicateScope": "project-a"},
    )
    assert plan["A1"].replicate == 2
    assert plan["A1"].matching_wells == 1


def test_duplicate_saved_reservations_fail_even_if_one_well_is_deleted() -> None:
    first = _row("p1", "A01", deleted_at="2026-02-01")
    second = _row("p2", "A01")
    for row in (first, second):
        row["custom_json"] = json.dumps(
            {
                "CultivationReplicateMode": "condition",
                "CultivationConditionKey": _key(row),
                "CultivationReplicateScope": "project-a",
                "CultivationReplicate": 1,
            }
        )
    with pytest.raises(DomainValidationError, match="Duplicate saved"):
        plan_condition_replicates(
            [first, second],
            target_plate_id="p2",
            registry={"CultivationReplicateScope": "project-a"},
        )


def test_deleted_and_blank_wells_do_not_consume_new_numbers() -> None:
    rows = [
        _row("old", "A01", deleted_at="2026-01-02"),
        _row("old", "A02", is_blank=1),
        _row("target", "A01", experiment_date="2026-03-01"),
        _row("target", "A02", is_blank=1),
    ]
    plan = plan_condition_replicates(
        rows, target_plate_id="target", registry={"CultivationReplicateScope": "project-a"}
    )
    assert list(plan) == ["A1"]
    assert plan["A1"].replicate == 1
    assert plan["A1"].matching_wells == 1


def test_legacy_custom_numbers_are_not_condition_reservations() -> None:
    old = _row(
        "old",
        "A01",
        custom_json='{"CultivationConditionKey":"stale","CultivationReplicate":1}',
    )
    target = _row("target", "A01", experiment_date="2026-02-01")
    plan = plan_condition_replicates(
        [old, target], target_plate_id="target", registry={"CultivationReplicateScope": "project-a"}
    )
    assert plan["A1"].replicate == 2


def test_scope_and_additional_fields_partition_matching_groups() -> None:
    same = _row("same", "A01", custom_json='{"oxygen":"aerobic"}')
    other_scope = _row("other", "A01", custom_json='{"oxygen":"aerobic"}')
    other_scope["plate_custom_json"] = (
        '{"cultivation_registry":{"CultivationReplicateScope":"project-b"}}'
    )
    other_field = _row("field", "A01", custom_json='{"oxygen":"anaerobic"}')
    target = _row("target", "A01", custom_json='{"oxygen":"aerobic"}')
    plan = plan_condition_replicates(
        [same, other_scope, other_field, target],
        target_plate_id="target",
        registry={
            "CultivationReplicateScope": "project-a",
            "CultivationConditionFields": " oxygen ",
        },
    )
    assert plan["A1"].replicate == 2
    assert plan["A1"].matching_wells == 2
    assert plan["A1"].matching_plates == 2
    assert plan["A1"].extra_fields == ("oxygen",)


def test_export_selection_reorders_stably_and_resets_for_subset() -> None:
    early = _row("early", "A01", experiment_date="2026-01-01", replicate=7)
    late = _row("late", "A01", experiment_date="2026-02-01", replicate=1)
    late["custom_json"] = json.dumps(
        {
            "CultivationReplicateMode": "condition",
            "CultivationConditionKey": "stale saved identity",
            "CultivationReplicate": 9,
            "Cultivation": "old-R9",
        }
    )
    before = deepcopy((early, late))
    forward = plan_export_condition_replicates([early, late])
    backward = plan_export_condition_replicates([late, early])
    assert forward == backward
    assert forward[("early", "A1")].replicate == 1
    assert forward[("late", "A1")].replicate == 2
    assert forward[("late", "A1")].matching_wells == 2
    assert forward[("late", "A1")].matching_plates == 2
    assert plan_export_condition_replicates([late])[("late", "A1")].replicate == 1
    assert (early, late) == before


def test_export_selection_resets_per_condition_and_scope() -> None:
    first = _row("p1", "A01")
    second = _row("p2", "A01", experiment_date="2026-02-01")
    different_medium = _row("p3", "A01", medium="LB")
    different_scope = _row("p4", "A01")
    different_scope["plate_custom_json"] = json.dumps(
        {"cultivation_registry": {"CultivationReplicateScope": "other project"}}
    )
    plan = plan_export_condition_replicates([different_scope, different_medium, second, first])
    assert {identity: item.replicate for identity, item in plan.items()} == {
        ("p1", "A1"): 1,
        ("p2", "A1"): 2,
        ("p3", "A1"): 1,
        ("p4", "A1"): 1,
    }
    assert plan[("p1", "A1")].matching_wells == 2
    assert plan[("p3", "A1")].matching_wells == 1


def test_export_selection_uses_consistent_field_union_with_saved_fallback() -> None:
    selected = [
        _row(
            "p1",
            "A01",
            custom_json='{"oxygen":"aerobic","batch":"b1"}',
            plate_custom_json=json.dumps(
                {"cultivation_registry": {"CultivationConditionFields": "oxygen"}}
            ),
        ),
        _row(
            "p2",
            "A01",
            custom_json='{"oxygen":"aerobic","batch":"b1"}',
            plate_custom_json=json.dumps(
                {"cultivation_registry": {"CultivationConditionFields": ["batch"]}}
            ),
        ),
        _row(
            "p3",
            "A01",
            custom_json=json.dumps(
                {
                    "oxygen": "aerobic",
                    "batch": "b1",
                    "CultivationConditionFields": ["shaking"],
                }
            ),
            plate_custom_json='{"cultivation_registry":{}}',
        ),
    ]
    plan = plan_export_condition_replicates(selected, extra_fields=(" batch ",))
    assert {item.extra_fields for item in plan.values()} == {("batch", "oxygen", "shaking")}
    assert [plan[(f"p{index}", "A1")].replicate for index in (1, 2, 3)] == [1, 2, 3]


def test_export_selection_missing_conditions_are_singletons_and_skips_blank_deleted() -> None:
    rows = [
        _row("p1", "A01", strain=None),
        _row("p2", "A01", strain=None),
        _row("p3", "A01", medium=None),
        _row("p4", "A01", is_blank=1),
        _row("p5", "A01", deleted_at="2026-02-01"),
    ]
    plan = plan_export_condition_replicates(rows)
    assert set(plan) == {("p1", "A1"), ("p2", "A1"), ("p3", "A1")}
    assert {item.replicate for item in plan.values()} == {1}
    assert {item.matching_wells for item in plan.values()} == {1}


def test_export_selection_groups_micro_spellings_without_converting_scales() -> None:
    spellings = ("ug/mL", "µg/mL", "μg/mL", "Œºg/mL", "Âµg/mL", "Î¼g/mL")
    rows = [
        _row(
            f"p{index}",
            "A01",
            concentration_unit=spelling,
            experiment_date=f"2026-01-{index:02d}",
        )
        for index, spelling in enumerate(spellings, start=1)
    ]
    rows.append(_row("mg", "A01", concentration_unit="mg/mL"))
    before = deepcopy(rows)

    plan = plan_export_condition_replicates(list(reversed(rows)))

    assert [plan[(f"p{index}", "A1")].replicate for index in range(1, 7)] == [1, 2, 3, 4, 5, 6]
    assert {plan[(f"p{index}", "A1")].matching_wells for index in range(1, 7)} == {6}
    assert {plan[(f"p{index}", "A1")].matching_plates for index in range(1, 7)} == {6}
    assert plan[("mg", "A1")].replicate == 1
    assert plan[("mg", "A1")].matching_wells == 1
    assert rows == before


def test_unit_matching_is_opt_in_and_applies_to_secondary_and_other_unit_fields() -> None:
    canonical = _row(
        "p1",
        "A01",
        concentration_unit="ug/mL",
        inoculum_unit="uL",
        temperature_unit="uC",
        custom_json='{"treatment_2":"drug B","conc_2":2,"unit_2":"uM"}',
    )
    alternate = _row(
        "p2",
        "A01",
        concentration_unit="Œºg/mL",
        inoculum_unit="µL",
        temperature_unit="μC",
        custom_json='{"treatment_2":"drug B","conc_2":2,"unit_2":"Î¼M"}',
    )
    before = deepcopy((canonical, alternate))

    literal = cultivation_condition_key(alternate, alternate, "project-a")
    assert literal == cultivation_condition_key(
        alternate, alternate, "project-a", normalize_units=False
    )
    assert literal != cultivation_condition_key(canonical, canonical, "project-a")
    assert cultivation_condition_key(
        alternate, alternate, "project-a", normalize_units=True
    ) == cultivation_condition_key(canonical, canonical, "project-a", normalize_units=True)
    plan = plan_export_condition_replicates([alternate, canonical])
    assert [plan[("p1", "A1")].replicate, plan[("p2", "A1")].replicate] == [1, 2]
    assert (canonical, alternate) == before


def test_export_precision_groups_equivalent_doses_across_plates_in_date_order() -> None:
    rows = [
        _row("p4", "A01", concentration="0.094", experiment_date="2026-01-04"),
        _row("p2", "A01", concentration="0.19", experiment_date="2026-01-02"),
        _row("p3", "A01", concentration="0.09375", experiment_date="2026-01-03"),
        _row("p1", "A01", concentration=0.1875, experiment_date="2026-01-01"),
    ]
    before = deepcopy(rows)
    plan = plan_export_condition_replicates(rows, concentration_significant_figures=2)
    assert [
        (plan[(f"p{n}", "A1")].replicate, plan[(f"p{n}", "A1")].matching_wells) for n in range(1, 5)
    ] == [
        (1, 2),
        (2, 2),
        (1, 2),
        (2, 2),
    ]
    assert {item.matching_plates for item in plan.values()} == {2}
    assert plan == plan_export_condition_replicates(
        list(reversed(rows)), concentration_significant_figures=2
    )
    assert rows == before


def test_export_precision_keeps_twofold_neighbors_and_exact_mode_separate() -> None:
    rows = [
        _row("p1", "A01", concentration="0.1875"),
        _row("p2", "A01", concentration="0.19"),
        _row("p3", "A01", concentration="0.09375"),
        _row("p4", "A01", concentration="0.094"),
    ]
    rounded = plan_export_condition_replicates(rows, concentration_significant_figures=2)
    exact = plan_export_condition_replicates(rows)
    assert rounded[("p1", "A1")].condition_key == rounded[("p2", "A1")].condition_key
    assert rounded[("p3", "A1")].condition_key == rounded[("p4", "A1")].condition_key
    assert rounded[("p1", "A1")].condition_key != rounded[("p3", "A1")].condition_key
    assert {item.matching_wells for item in exact.values()} == {1}


def test_precision_applies_to_secondary_tertiary_and_reordered_treatments() -> None:
    first = _row(
        "p1",
        "A01",
        custom_json=json.dumps(
            {
                "treatment_2": "drug B",
                "conc_2": "0.1875",
                "unit_2": "uM",
                "treatment_3": "drug C",
                "conc_3": "0.09375",
                "unit_3": "uM",
            }
        ),
    )
    reordered = _row(
        "p2",
        "A01",
        treatment="drug C",
        concentration="0.094",
        concentration_unit="uM",
        custom_json=json.dumps(
            {
                "treatment_2": "drug A",
                "conc_2": "0.1",
                "unit_2": "mg/mL",
                "treatment_3": "drug B",
                "conc_3": "0.19",
                "unit_3": "uM",
            }
        ),
    )
    assert cultivation_condition_key(
        first, first, "project-a", concentration_significant_figures=2
    ) == cultivation_condition_key(
        reordered, reordered, "project-a", concentration_significant_figures=2
    )
    assert _key(first) != _key(reordered)


def test_precision_does_not_round_other_scientific_or_additional_fields() -> None:
    first = _row("p1", "A01", concentration="0.1875", inoculum_size="0.1875")
    second = _row("p2", "A01", concentration="0.19", inoculum_size="0.19")
    assert cultivation_condition_key(
        first, first, "project-a", concentration_significant_figures=2
    ) != cultivation_condition_key(second, second, "project-a", concentration_significant_figures=2)
    second["inoculum_size"] = first["inoculum_size"]
    first["custom_json"] = '{"batch_concentration":"0.1875"}'
    second["custom_json"] = '{"batch_concentration":"0.19"}'
    assert cultivation_condition_key(
        first, first, "project-a", ("batch_concentration",), concentration_significant_figures=2
    ) != cultivation_condition_key(
        second, second, "project-a", ("batch_concentration",), concentration_significant_figures=2
    )


def test_matching_concentration_is_readable_half_up_and_context_independent() -> None:
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        assert matching_concentration("0.1875", 2) == "0.19"
        assert matching_concentration(0.09375, 2) == "0.094"
        assert matching_concentration("0.125", 2) == "0.13"
        assert matching_concentration("-0.125", 2) == "-0.13"
        assert matching_concentration(Decimal("0.1900"), 2) == "0.19"
        assert matching_concentration("0.1875") == "0.1875"
        assert matching_concentration("0.1900") == "0.19"
    assert matching_concentration(None, 2) == ""
    assert matching_concentration("", 2) == ""
    assert matching_concentration("below detection", 2) == "below detection"
    assert matching_concentration(0, 2) == "0"
    assert matching_concentration(-0.0, 2) == "0"
    assert matching_concentration("1e100000", 2) == "1e100000"
    assert matching_concentration("1e-100000", 2) == "1e-100000"
    assert matching_concentration("0.000000001", 2) != matching_concentration(0, 2)


@pytest.mark.parametrize("precision", (True, False, 0, 13, -1, 2.0, "2", Decimal("2")))
def test_invalid_concentration_precision_is_structured(precision: object) -> None:
    with pytest.raises(DomainValidationError, match="significant figures"):
        validate_concentration_precision(precision)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="significant figures"):
        matching_concentration("0.1875", precision)  # type: ignore[arg-type]
    row = _row("p1", "A01")
    with pytest.raises(DomainValidationError, match="significant figures"):
        plan_export_condition_replicates(
            [row],
            concentration_significant_figures=precision,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("dose", (float("nan"), float("inf"), Decimal("-Infinity"), "-sNaN"))
def test_matching_concentration_rejects_nonfinite_values(dose: object) -> None:
    with pytest.raises(DomainValidationError, match="finite"):
        matching_concentration(dose, 2)


def test_precision_default_keeps_saved_fingerprint_and_clear_override_behavior() -> None:
    row = _row("p1", "A01", concentration="0.1875")
    baseline = cultivation_condition_key(row, row, "project-a")
    assert baseline == cultivation_condition_key(
        row, row, "project-a", concentration_significant_figures=None
    )
    assert "concentration_significant_figures" not in json.loads(baseline)
    saved = plan_condition_replicates(
        [row], target_plate_id="p1", registry={"CultivationReplicateScope": "project-a"}
    )["A1"]
    assert saved.condition_key == baseline
    cleared = _row(
        "p2",
        "A01",
        concentration=None,
        custom_json='{"conc_1":"0.19"}',
        condition_custom_json='{"primary_condition_overrides":["concentration"]}',
    )
    clear_key = cultivation_condition_key(
        cleared, cleared, "project-a", concentration_significant_figures=2
    )
    assert json.loads(clear_key)["treatments"] == [["drug A", "", "mg/mL"]]
