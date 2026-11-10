from __future__ import annotations

import pandas as pd
import pytest

from plate_reader.application.demo import synthetic_mic_csv
from plate_reader.domain.growth.treatments import ADDITIONAL_TREATMENT_COLUMNS
from plate_reader.domain.mic import parse_mic_plate_csv
from plate_reader.ui.plate_editor import (
    _editor_safe_frame,
    _restore_structured_values,
    apply_growth_template,
    apply_mic_template,
    apply_plate_matrix,
    fill_layout,
    growth_layout_changes,
    growth_layout_frame,
    growth_layout_frame_from_wells,
    growth_template_layout,
    include_layout_columns,
    mic_layout_changes,
    mic_layout_frame,
    mic_layout_frame_from_snapshot,
    mic_template_layout,
    plate_matrix,
)


def test_universal_layout_columns_are_added_without_overwriting_plate_values() -> None:
    frame = growth_layout_frame()
    frame["Oxygen"] = "aerobic"

    updated = include_layout_columns(frame, ("oxygen", "Vessel"))

    assert "oxygen" not in updated
    assert set(updated["Oxygen"]) == {"aerobic"}
    assert set(updated["Vessel"]) == {""}


def test_growth_grid_and_table_share_physical_plate_order() -> None:
    frame = growth_layout_frame({"A1": "first", "H12": "last"})
    assert frame.shape == (96, 23)
    assert frame.iloc[0]["Well"] == "A1"
    assert frame.iloc[-1]["Well"] == "H12"

    grid = plate_matrix(frame, "Strain")
    grid.loc["A", "1"] = "strain-a"
    grid.loc["H", "12"] = "strain-h"
    synchronized = apply_plate_matrix(frame, "Strain", grid)

    assert synchronized.loc[0, "Strain"] == "strain-a"
    assert synchronized.loc[95, "Strain"] == "strain-h"


def test_fill_helpers_target_full_plate_row_and_column() -> None:
    frame = growth_layout_frame()
    frame = fill_layout(frame, "Media", "M9", "Full plate")
    frame = fill_layout(frame, "Strain", "row-b", "Row", "B")
    frame = fill_layout(frame, "Blank", True, "Column", 12)

    assert set(frame["Media"]) == {"M9"}
    assert set(frame.loc[frame["Well"].str.startswith("B"), "Strain"]) == {"row-b"}
    assert frame.loc[frame["Well"].str.endswith("12"), "Blank"].all()
    assert int(frame["Blank"].sum()) == 8


def test_growth_layout_conversion_preserves_every_legacy_field_and_custom_columns() -> None:
    frame = growth_layout_frame({"A1": "raw-a1"})
    frame["Oxygen"] = ""
    frame.loc[
        0,
        [
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
            "Oxygen",
        ],
    ] = [
        "sample a1",
        True,
        "m9",
        True,
        "group-a",
        "M9",
        "strain-a",
        0.02,
        "OD600",
        3,
        "note-a",
        "drug-a",
        2.5,
        "ug/mL",
        4.0,
        "anaerobic",
    ]

    change = growth_layout_changes(frame)[0]
    assert change.position == "A1"
    assert change.display_name == "sample a1"
    assert change.is_blank is True
    assert change.background_group == "m9"
    assert change.plot_selected is True
    assert change.grouping_label == "group-a"
    assert change.medium == "M9"
    assert change.strain == "strain-a"
    assert change.inoculum_size == 0.02
    assert change.inoculum_unit == "OD600"
    assert change.replicate == 3
    assert change.notes == "note-a"
    assert change.treatment == "drug-a"
    assert change.concentration == 2.5
    assert change.concentration_unit == "ug/mL"
    assert change.custom_fields == {
        **dict.fromkeys(ADDITIONAL_TREATMENT_COLUMNS.values()),
        "Oxygen": "anaerobic",
        "t0_added_min": 4.0,
    }


def test_persisted_growth_wells_rehydrate_every_editor_field() -> None:
    frame = growth_layout_frame_from_wells(
        (
            {
                "position": "A1",
                "raw_label": "raw-a1",
                "display_name": "sample-a1",
                "is_blank": 1,
                "background_group": "m9",
                "plot_selected": 1,
                "grouping_label": "group-a",
                "medium": "M9",
                "strain": "strain-a",
                "inoculum_size": 0.02,
                "inoculum_unit": "OD600",
                "replicate": 3,
                "notes": "note-a",
                "treatment": "drug-a",
                "concentration": 2.5,
                "concentration_unit": "ug/mL",
                "custom_json": '{"Oxygen":"low","t0_added_min":4.0}',
            },
        )
    )

    assert frame.shape == (96, 24)
    assert frame.loc[0].to_dict() == {
        "Well": "A1",
        "Raw label": "raw-a1",
        "Display name": "sample-a1",
        "Blank": True,
        "Background group": "m9",
        "Plot": True,
        "Group": "group-a",
        "Media": "M9",
        "Strain": "strain-a",
        "Inoculum size": 0.02,
        "Inoculum unit": "OD600",
        "Replicate": 3,
        "Notes": "note-a",
        "Treatment": "drug-a",
        "Concentration": 2.5,
        "Concentration unit": "ug/mL",
        **dict.fromkeys(ADDITIONAL_TREATMENT_COLUMNS),
        "T0 added (min)": 4.0,
        "Oxygen": "low",
    }
    assert frame.loc[95, "Well"] == "H12"


def test_mic_layout_conversion_keeps_raw_od_and_arbitrary_label_grids() -> None:
    frame = mic_layout_frame(parse_mic_plate_csv(synthetic_mic_csv()))
    frame["Oxygen"] = ""
    frame.loc[0, "Raw OD"] = 0.333
    frame.loc[0, "Oxygen"] = "low"
    frame.loc[0, "Display name"] = "A1 edited"

    change = mic_layout_changes(frame)[0]
    assert change.position == "A1"
    assert change.value_raw == 0.333
    assert change.display_name == "A1 edited"
    assert change.custom_labels == {"Oxygen": "low"}


def test_persisted_mic_layout_rehydrates_and_omits_immutable_raw_updates() -> None:
    source = mic_layout_frame(parse_mic_plate_csv(synthetic_mic_csv()))
    wells = tuple(
        {
            "well_id": f"well-{row['Well']}",
            "position": row["Well"],
            "display_name": "sample A1" if row["Well"] == "A1" else "",
            "is_blank": row["Blank"],
            "strain": row["Strain"],
            "treatment": row["Antibiotic / treatment"],
            "concentration": row["Concentration"],
            "concentration_unit": row["Concentration unit"],
            "medium": row["Media"],
            "replicate": row["Replicate"],
            "notes": "saved note" if row["Well"] == "A1" else "",
            "custom_json": '{"Oxygen":"low"}' if row["Well"] == "A1" else "{}",
        }
        for row in source.to_dict(orient="records")
    )
    readings = tuple(
        {"well_id": f"well-{row['Well']}", "value_raw": row["Raw OD"]}
        for row in source.to_dict(orient="records")
    )

    frame = mic_layout_frame_from_snapshot(wells, readings)
    change = mic_layout_changes(frame, include_raw=False)[0]

    assert frame.shape == (96, 12)
    assert frame.loc[0, "Display name"] == "sample A1"
    assert frame.loc[0, "Oxygen"] == "low"
    assert change.value_raw is None
    assert change.notes == "saved note"
    assert change.custom_labels == {"Oxygen": "low"}


def test_growth_template_round_trip_preserves_target_raw_labels() -> None:
    source = growth_layout_frame({"A1": "source raw"})
    source["Oxygen"] = ""
    source.loc[0, "Display name"] = "template name"
    source.loc[0, "Strain"] = "template strain"
    source.loc[0, "T0 added (min)"] = 7.5
    source.loc[0, "Oxygen"] = "low"

    layout = growth_template_layout(source)
    target = growth_layout_frame({"A1": "new imported raw", "B2": "another raw"})
    applied = apply_growth_template(target, layout)

    assert len(layout) == 96
    assert "raw_label" not in layout[0]
    assert applied.loc[0, "Raw label"] == "new imported raw"
    assert applied.loc[0, "Display name"] == "template name"
    assert applied.loc[0, "Strain"] == "template strain"
    assert applied.loc[0, "T0 added (min)"] == 7.5
    assert applied.loc[0, "Oxygen"] == "low"
    assert applied.loc[13, "Raw label"] == "another raw"


def test_mic_template_round_trip_preserves_target_raw_od() -> None:
    source = mic_layout_frame(parse_mic_plate_csv(synthetic_mic_csv()))
    source["Oxygen"] = ""
    source.loc[0, "Raw OD"] = 0.111
    source.loc[0, "Strain"] = "template strain"
    source.loc[0, "Oxygen"] = "anaerobic"
    layout = mic_template_layout(source)

    target = mic_layout_frame(parse_mic_plate_csv(synthetic_mic_csv()))
    target.loc[0, "Raw OD"] = 0.987
    applied = apply_mic_template(target, layout)

    assert len(layout) == 96
    assert "value_raw" not in layout[0]
    assert applied.loc[0, "Raw OD"] == 0.987
    assert applied.loc[0, "Strain"] == "template strain"
    assert applied.loc[0, "Oxygen"] == "anaerobic"


def test_applying_incomplete_template_is_rejected() -> None:
    frame = growth_layout_frame()
    with pytest.raises(ValueError, match="each A1-H12"):
        apply_growth_template(frame, ({"position": "A1"},))
    with pytest.raises(ValueError, match="each A1-H12"):
        apply_mic_template(frame, ({"position": "A1"},))


def test_additional_growth_treatments_edit_and_template_roundtrip() -> None:
    frame = growth_layout_frame({"A1": "raw A1"})
    for slot, treatment, concentration, unit in (
        (2, "drug B", 2.5, "uM"),
        (3, "drug C", 0.0, "mg/mL"),
    ):
        frame = fill_layout(frame, f"Treatment {slot}", treatment, "Row", "A")
        grid = plate_matrix(frame, f"Concentration {slot}")
        grid.loc["A", "1"] = concentration
        frame = apply_plate_matrix(frame, f"Concentration {slot}", grid)
        frame.loc[0, f"Concentration unit {slot}"] = unit
    frame["Oxygen"] = "aerobic"

    change = growth_layout_changes(frame)[0]
    assert change.custom_fields == {
        "treatment_2": "drug B",
        "conc_2": 2.5,
        "unit_2": "uM",
        "treatment_3": "drug C",
        "conc_3": 0.0,
        "unit_3": "mg/mL",
        "Oxygen": "aerobic",
    }
    applied = apply_growth_template(
        growth_layout_frame({"A1": "new raw"}), growth_template_layout(frame)
    )
    assert applied.loc[0, "Raw label"] == "new raw"
    assert growth_layout_changes(applied)[0].custom_fields == change.custom_fields
    safe = _editor_safe_frame(applied)
    assert str(safe["Concentration 2"].dtype) == "Float64"
    assert safe.loc[0, "Concentration 3"] == 0.0


def test_growth_alias_columns_collapse_and_clearing_does_not_revive_fallbacks() -> None:
    condition = {"treatment_2": "condition drug", "conc_2": 5, "unit_2": "uM"}
    frame = growth_layout_frame_from_wells(
        (
            {
                "position": "A1",
                "condition_custom_json": condition,
                "custom_json": {
                    "Treatment 2": "well alias",
                    "Concentration 3": "unknown legacy value",
                    "treatment_3": "drug C",
                    "Concentration unit 3": "mg/mL",
                    "Oxygen": "low",
                },
            },
        )
    )
    assert frame.loc[0, "Treatment 2"] == "condition drug"
    assert "treatment_2" not in frame
    assert "conc_2" not in frame
    assert frame.columns.is_unique
    assert _editor_safe_frame(frame).loc[0, "Concentration 3"] == "unknown legacy value"
    assert growth_layout_changes(frame)[0].custom_fields["conc_3"] == "unknown legacy value"

    for label in ADDITIONAL_TREATMENT_COLUMNS:
        frame.loc[0, label] = None
    saved = growth_layout_changes(frame)[0].custom_fields
    assert saved == {
        **dict.fromkeys(ADDITIONAL_TREATMENT_COLUMNS.values()),
        "Treatment 2": "well alias",
        "Oxygen": "low",
    }
    reloaded = growth_layout_frame_from_wells(
        (
            {
                "position": "A1",
                "custom_json": saved,
                "condition_custom_json": condition,
            },
        )
    )
    assert all(pd.isna(reloaded.loc[0, label]) for label in ADDITIONAL_TREATMENT_COLUMNS)


def test_legacy_staged_growth_aliases_upgrade_without_duplicate_universal_columns() -> None:
    frame = growth_layout_frame().drop(columns=list(ADDITIONAL_TREATMENT_COLUMNS))
    frame["treatment_2"] = "legacy drug"
    frame["conc_2"] = "2.5"
    frame["unit_2"] = "uM"
    frame["Oxygen"] = "low"

    updated = include_layout_columns(frame, ("Treatment 2", "treatment_2", "Concentration 2"))
    assert updated.columns.is_unique
    assert "treatment_2" not in updated
    assert "conc_2" not in updated
    assert updated.loc[0, "Treatment 2"] == "legacy drug"
    # Fixed displayed fields are authoritative, including an explicit clear.
    updated["treatment_2"] = "stale alias"
    updated.loc[0, "Treatment 2"] = ""
    custom = growth_layout_changes(updated)[0].custom_fields
    assert custom["treatment_2"] is None
    assert custom["conc_2"] == "2.5"
    assert custom["Oxygen"] == "low"


def test_legacy_growth_template_aliases_load_into_fixed_treatment_columns() -> None:
    layout = list(growth_template_layout(growth_layout_frame()))
    layout[0]["custom_fields"] = {
        "Treatment 2": "legacy drug",
        "Concentration 2": "not specified",
        "Concentration unit 2": "uM",
        "Oxygen": "low",
    }
    frame = apply_growth_template(growth_layout_frame(), layout)
    assert frame.columns.is_unique
    assert "treatment_2" not in frame
    assert frame.loc[0, "Treatment 2"] == "legacy drug"
    assert frame.loc[0, "Concentration 2"] == "not specified"
    custom = growth_layout_changes(frame)[0].custom_fields
    assert custom["treatment_2"] == "legacy drug"
    assert custom["conc_2"] == "not specified"
    assert "Treatment 2" not in custom
    assert custom["Oxygen"] == "low"


def test_unrelated_edits_preserve_conflicting_aliases_and_structured_json_metadata() -> None:
    custom = {
        "treatment_2": "canonical drug",
        "Drug 2": "different legacy drug",
        "CultivationIdentity": {"number": "01", "history": [{"old": "00"}]},
        "History": [{"revision": 1}, {"revision": 2}],
    }
    frame = growth_layout_frame_from_wells(({"position": "A1", "custom_json": custom},))
    assert "Drug 2" not in frame
    frame = fill_layout(frame, "Strain", "new strain", "Full plate")
    grid = plate_matrix(frame, "Notes")
    grid.loc["A", "1"] = "grid edit"
    frame = apply_plate_matrix(frame, "Notes", grid)
    changes = growth_layout_changes(frame)
    assert changes[0].custom_fields == {
        **dict.fromkeys(ADDITIONAL_TREATMENT_COLUMNS.values()),
        **custom,
    }
    template = growth_template_layout(frame)
    applied = apply_growth_template(growth_layout_frame(), template)
    assert growth_layout_changes(applied)[0].custom_fields == changes[0].custom_fields

    # Arrow receives display strings; unchanged container text restores its JSON type.
    displayed = _editor_safe_frame(frame)
    displayed.loc[0, "Notes"] = "unrelated edit"
    restored = _restore_structured_values(displayed, frame)
    after = growth_layout_changes(restored)[0]
    assert after.notes == "unrelated edit"
    assert after.custom_fields["CultivationIdentity"] == custom["CultivationIdentity"]
    assert after.custom_fields["History"] == custom["History"]
    assert after.custom_fields["Drug 2"] == "different legacy drug"


def test_condition_only_non_slot_metadata_is_not_copied_to_editable_well_json() -> None:
    frame = growth_layout_frame_from_wells(
        (
            {
                "position": "A1",
                "custom_json": {"Oxygen": "low"},
                "condition_custom_json": {
                    "treatment_2": "drug B",
                    "conc_2": "legacy",
                    "unit_2": "uM",
                    "primary_condition_overrides": ["treatment"],
                    "Oxygen": "stale",
                    "Cleared custom field": "must not revive",
                    "t0_added_min": 7.5,
                },
            },
        )
    )
    assert frame.loc[0, "Treatment 2"] == "drug B"
    assert frame.loc[0, "Concentration 2"] == "legacy"
    assert frame.loc[0, "Oxygen"] == "low"
    assert frame.loc[0, "T0 added (min)"] is None
    assert "primary_condition_overrides" not in frame
    assert "Cleared custom field" not in frame
    custom = growth_layout_changes(frame)[0].custom_fields
    assert custom["treatment_2"] == "drug B"
    assert custom["Oxygen"] == "low"
    assert "primary_condition_overrides" not in custom
    assert "Cleared custom field" not in custom
    assert "t0_added_min" not in custom


def test_mic_custom_concentration_labels_keep_text_and_original_spelling() -> None:
    frame = mic_layout_frame(parse_mic_plate_csv(synthetic_mic_csv()))
    frame["Raw label"] = "user-defined MIC label"
    frame["Concentration 2"] = "0002"
    edited = include_layout_columns(frame, ("Drug 2",), is_growth=False)
    assert "Drug 2" in edited and "Treatment 2" not in edited
    assert _editor_safe_frame(edited, is_growth=False).at[0, "Concentration 2"] == "0002"
