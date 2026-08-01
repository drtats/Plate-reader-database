from __future__ import annotations

import pytest

from plate_reader.application.services import (
    BuildGrowthBackgroundGroupsService,
    GrowthBackgroundGroupSource,
)
from plate_reader.domain.common.plate import PLATE_96


def test_background_groups_are_derived_in_physical_order_with_plate_fallback() -> None:
    wells = tuple(
        {
            "position": position.label,
            "medium": "M9" if position.label == "A1" else "",
            "strain": "strain-a",
            "grouping_label": "group-a",
            "treatment": "drug-a",
        }
        for position in reversed(PLATE_96.positions())
    )

    changes = BuildGrowthBackgroundGroupsService().execute(
        wells, GrowthBackgroundGroupSource.MEDIUM
    )

    assert len(changes) == 96
    assert (changes[0].position, changes[0].background_group) == ("A1", "M9")
    assert (changes[-1].position, changes[-1].background_group) == ("H12", "plate")
    assert all(change.display_name is None for change in changes)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (GrowthBackgroundGroupSource.STRAIN, "strain-a"),
        (GrowthBackgroundGroupSource.GROUP, "group-a"),
        (GrowthBackgroundGroupSource.TREATMENT, "drug-a"),
    ],
)
def test_every_supported_background_source_maps_its_persisted_field(
    source: GrowthBackgroundGroupSource, expected: str
) -> None:
    wells = tuple(
        {
            "position": position.label,
            "medium": "M9",
            "strain": "strain-a",
            "grouping_label": "group-a",
            "treatment": "drug-a",
        }
        for position in PLATE_96.positions()
    )

    assert (
        BuildGrowthBackgroundGroupsService().execute(wells, source)[0].background_group == expected
    )


def test_background_group_derivation_rejects_partial_layout() -> None:
    with pytest.raises(ValueError, match="every A1-H12"):
        BuildGrowthBackgroundGroupsService().execute(
            ({"position": "A1", "medium": "M9"},),
            GrowthBackgroundGroupSource.MEDIUM,
        )
