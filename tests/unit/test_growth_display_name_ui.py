from __future__ import annotations

from datetime import date

from plate_reader.application.services import (
    GrowthDisplayNameAction,
    GrowthDisplayNameChange,
    GrowthDisplayNamePreview,
)
from plate_reader.ui.growth_display_names import (
    apply_growth_display_name_preview,
    growth_display_name_metadata,
    growth_display_name_wells,
)
from plate_reader.ui.plate_editor import growth_layout_frame, plate_matrix


def test_editor_adapter_preserves_first_class_and_custom_name_tokens() -> None:
    frame = growth_layout_frame({"A1": "raw-a1"})
    frame["Oxygen"] = ""
    frame.loc[0, "Display name"] = "existing"
    frame.loc[0, "Strain"] = "strain-a"
    frame.loc[0, "Concentration"] = 0.125
    frame.loc[0, "T0 added (min)"] = 5.0
    frame.loc[0, "Oxygen"] = "low"

    wells = growth_display_name_wells(frame)

    assert wells[0] == {
        "position": "A1",
        "display_name": "existing",
        "raw_label": "raw-a1",
        "strain": "strain-a",
        "treatment": None,
        "concentration": 0.125,
        "concentration_unit": None,
        "medium": "LB",
        "grouping_label": None,
        "inoculum_size": None,
        "inoculum_unit": "OD600",
        "replicate": 1,
        "t0_added_min": 5.0,
        "custom_fields": {"Oxygen": "low"},
    }
    assert len(wells) == 96


def test_applying_preview_changes_only_listed_display_names() -> None:
    frame = growth_layout_frame()
    preview = GrowthDisplayNamePreview(
        (
            GrowthDisplayNameChange("A1", "", "new-a1", GrowthDisplayNameAction.ADD),
            GrowthDisplayNameChange("H12", "", "new-h12", GrowthDisplayNameAction.ADD),
        )
    )

    updated = apply_growth_display_name_preview(frame, preview)

    assert updated.loc[0, "Display name"] == "new-a1"
    assert updated.loc[95, "Display name"] == "new-h12"
    assert updated.loc[1, "Display name"] == ""
    assert updated.drop(columns="Display name").equals(frame.drop(columns="Display name"))
    grid = plate_matrix(updated, "Display name")
    assert grid.loc["A", "1"] == "new-a1"
    assert grid.loc["H", "12"] == "new-h12"


def test_plate_metadata_adapter_accepts_wizard_and_persisted_names() -> None:
    wizard = growth_display_name_metadata(
        {
            "experiment_name": "Wizard name",
            "plate_name": "Plate 1",
            "experiment_date": date(2026, 8, 1),
            "tags": ("growth",),
        }
    )
    persisted = growth_display_name_metadata(
        {"name": "Saved name", "plate_name": "Plate 2", "tags": ("saved",)}
    )

    assert wizard["experiment_name"] == "Wizard name"
    assert wizard["experiment_date"] == date(2026, 8, 1)
    assert persisted["experiment_name"] == "Saved name"
    assert persisted["tags"] == ("saved",)
