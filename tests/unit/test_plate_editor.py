from __future__ import annotations

from plate_reader.application.demo import synthetic_mic_csv
from plate_reader.domain.mic import parse_mic_plate_csv
from plate_reader.ui.plate_editor import (
    apply_plate_matrix,
    fill_layout,
    growth_layout_changes,
    growth_layout_frame,
    growth_layout_frame_from_wells,
    mic_layout_changes,
    mic_layout_frame,
    mic_layout_frame_from_snapshot,
    plate_matrix,
)


def test_growth_grid_and_table_share_physical_plate_order() -> None:
    frame = growth_layout_frame({"A1": "first", "H12": "last"})
    assert frame.shape == (96, 17)
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
    assert change.custom_fields == {"Oxygen": "anaerobic", "t0_added_min": 4.0}


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

    assert frame.shape == (96, 18)
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
