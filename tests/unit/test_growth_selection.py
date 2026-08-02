from __future__ import annotations

from collections.abc import Mapping

import pytest

from plate_reader.application.services import (
    GrowthSelectionOperation,
    GrowthWellFilter,
    GrowthWellSelectionService,
    combine_growth_selection,
    growth_selection_fields,
    normalize_growth_selection,
)
from plate_reader.domain.common.plate import PLATE_96


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (GrowthSelectionOperation.REPLACE, ("A2", "B1")),
        (GrowthSelectionOperation.ADD, ("A1", "A2", "B1")),
        (GrowthSelectionOperation.REMOVE, ("A1",)),
        (GrowthSelectionOperation.KEEP_ONLY, ("A2",)),
    ],
)
def test_selection_operations_return_unique_physical_order(
    operation: GrowthSelectionOperation, expected: tuple[str, ...]
) -> None:
    assert combine_growth_selection(("A2", "A1", "A2"), ("B1", "A2"), operation) == expected


def test_filters_use_or_within_a_field_and_and_across_fields() -> None:
    wells = growth_wells()

    selected = GrowthWellSelectionService().execute(
        wells,
        (),
        (
            GrowthWellFilter("strain", ("STRAIN-A", "strain-b")),
            GrowthWellFilter("medium", ("M9",)),
        ),
        GrowthSelectionOperation.REPLACE,
    )

    assert selected == ("A1", "A2")


def test_custom_field_filters_and_explicit_operations_share_one_contract() -> None:
    wells = growth_wells()
    service = GrowthWellSelectionService()

    added = service.execute(
        wells,
        ("H12",),
        (GrowthWellFilter("custom:Oxygen", ("low", "high")),),
        GrowthSelectionOperation.ADD,
    )
    removed = service.execute(
        wells,
        added,
        (GrowthWellFilter("custom:Oxygen", ("HIGH",)),),
        GrowthSelectionOperation.REMOVE,
    )

    assert added == ("A1", "A2", "B1", "H12")
    assert removed == ("A1", "A2", "H12")


def test_available_filter_fields_normalize_values_without_rewriting_wells() -> None:
    wells = growth_wells()
    original = tuple(dict(well) for well in wells)

    fields = {field.key: field for field in growth_selection_fields(wells)}

    assert fields["strain"].values == ("strain-a", "strain-b")
    assert fields["concentration"].values == ("1.0", "2.0", "10.0")
    assert fields["custom:Oxygen"].label == "Oxygen (custom)"
    assert fields["custom:Oxygen"].values == ("high", "low")
    assert tuple(dict(well) for well in wells) == original


def test_empty_filters_leave_the_current_selection_unchanged() -> None:
    wells = growth_wells()

    assert GrowthWellSelectionService().execute(
        wells,
        ("B1", "A1"),
        (GrowthWellFilter("strain", ()),),
        GrowthSelectionOperation.REPLACE,
    ) == ("A1", "B1")


def test_selection_normalization_accepts_case_and_leading_zeroes() -> None:
    assert normalize_growth_selection(growth_wells(), ("h012", "a01")) == ("A1", "H12")


def test_selection_rejects_partial_duplicate_or_unknown_layout_data() -> None:
    wells = growth_wells()
    with pytest.raises(ValueError, match="every A1-H12"):
        normalize_growth_selection(wells[:-1], ())
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_growth_selection((*wells[:-1], wells[0]), ())
    with pytest.raises(ValueError, match="Unknown Growth selection field"):
        GrowthWellSelectionService().execute(
            wells,
            (),
            (GrowthWellFilter("missing", ("value",)),),
            GrowthSelectionOperation.REPLACE,
        )


def growth_wells() -> tuple[Mapping[str, object], ...]:
    overrides: dict[str, dict[str, object]] = {
        "A1": {
            "display_name": "sample-a1",
            "raw_label": "raw-a1",
            "strain": "strain-a",
            "treatment": "drug",
            "concentration": 1.0,
            "medium": "M9",
            "grouping_label": "group-1",
            "replicate": 1,
            "custom_json": '{"Oxygen":"low"}',
        },
        "A2": {
            "strain": "strain-a",
            "treatment": "drug",
            "concentration": 2.0,
            "medium": "M9",
            "grouping_label": "group-1",
            "replicate": 2,
            "custom_json": '{"Oxygen":"low"}',
        },
        "B1": {
            "strain": "strain-b",
            "treatment": "vehicle",
            "concentration": 10.0,
            "medium": "LB",
            "grouping_label": "group-2",
            "replicate": 1,
            "custom_json": '{"Oxygen":"high"}',
        },
    }
    return tuple(
        {"position": position.label, "custom_json": "{}", **overrides.get(position.label, {})}
        for position in PLATE_96.positions()
    )
