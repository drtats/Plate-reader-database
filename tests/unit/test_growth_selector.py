from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import pytest

from plate_reader.domain.common.plate import PLATE_96
from plate_reader.ui.growth_selector import (
    growth_selection_from_grid,
    growth_selection_from_list,
    growth_selection_grid,
    growth_selection_list,
    growth_selection_reference,
    reference_plate_fields,
)


def test_grid_and_list_round_trip_the_same_canonical_selection() -> None:
    wells = growth_wells()
    selected = ("H12", "A1", "B2")

    grid_result = growth_selection_from_grid(growth_selection_grid(selected))
    list_result = growth_selection_from_list(growth_selection_list(wells, selected))

    assert grid_result == ("A1", "B2", "H12")
    assert list_result == grid_result


def test_reference_plate_shows_the_chosen_layout_field() -> None:
    wells = growth_wells()

    display_names = growth_selection_reference(wells, field_key="display_name")
    strains = growth_selection_reference(wells, field_key="strain")
    concentrations = growth_selection_reference(wells, field_key="concentration")
    custom = growth_selection_reference(wells, field_key="custom:Oxygen")
    positions = growth_selection_reference(wells, field_key="position")

    assert display_names.loc["A", "1"] == "display-a1"
    assert display_names.loc["A", "2"] == "—"
    assert strains.loc["A", "1"] == "strain-a"
    assert concentrations.loc["A", "1"] == "1.0"
    assert custom.loc["A", "1"] == "low"
    assert positions.loc["A", "3"] == "A3"


def test_reference_plate_offers_present_layout_and_custom_fields() -> None:
    fields = dict(reference_plate_fields(growth_wells()))

    assert fields["position"] == "Well position"
    assert fields["display_name"] == "Display name"
    assert fields["strain"] == "Strain"
    assert fields["concentration"] == "Concentration"
    assert fields["custom:Oxygen"] == "Oxygen (custom)"
    assert "medium" not in fields


def test_grid_and_list_reject_incomplete_or_duplicate_shapes() -> None:
    grid = growth_selection_grid(())
    with pytest.raises(ValueError, match="rows A-H"):
        growth_selection_from_grid(grid.drop(index="H"))

    selection_list = growth_selection_list(growth_wells(), ())
    selection_list.loc[1, "Well"] = "A1"
    with pytest.raises(ValueError, match="Duplicate"):
        growth_selection_from_list(selection_list)

    with pytest.raises(ValueError, match="requires Well and Selected"):
        growth_selection_from_list(pd.DataFrame({"Well": ["A1"]}))


def growth_wells() -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "position": position.label,
            "display_name": "display-a1" if position.label == "A1" else "",
            "raw_label": "raw-a2" if position.label == "A2" else "",
            "strain": "strain-a" if position.label == "A1" else "",
            "concentration": 1.0 if position.label == "A1" else None,
            "custom_json": '{"Oxygen":"low"}' if position.label == "A1" else "{}",
        }
        for position in PLATE_96.positions()
    )
