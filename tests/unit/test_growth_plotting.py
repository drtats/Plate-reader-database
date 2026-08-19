from __future__ import annotations

import pytest

from plate_reader.application.contracts import PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_plotting import (
    GrowthPlotLabelOptions,
    PrepareGrowthPlotDataService,
    growth_plot_label_fields,
)
from plate_reader.domain.common import IssueCode


def test_plot_data_applies_background_and_saved_manual_subtraction() -> None:
    snapshot = growth_snapshot(manual_subtraction=0.02)

    result = PrepareGrowthPlotDataService().execute(
        snapshot,
        (background_row(mean=0.1),),
        ("A1",),
        corrected=True,
    )

    assert len(result.points) == 1
    assert result.points[0].value == pytest.approx(0.18)
    assert result.points[0].value_raw == 0.3
    assert result.points[0].background_mean == 0.1
    assert result.points[0].correction_applied is True
    assert result.points[0].label == "sample A1"
    assert result.points[0].time_index == 0
    assert result.points[0].elapsed_microseconds == 0
    assert result.issues == ()


def test_plot_data_marks_raw_fallback_when_background_is_missing() -> None:
    result = PrepareGrowthPlotDataService().execute(
        growth_snapshot(),
        (),
        ("A1",),
        corrected=True,
    )

    assert result.points[0].value == result.points[0].value_raw == 0.3
    assert result.points[0].background_mean is None
    assert result.points[0].correction_applied is False
    assert result.correction_requested is True
    assert [issue.code for issue in result.issues] == [IssueCode.MISSING_BACKGROUND]


def test_raw_plot_data_does_not_apply_background_or_manual_subtraction() -> None:
    result = PrepareGrowthPlotDataService().execute(
        growth_snapshot(manual_subtraction=0.2),
        (background_row(mean=0.1),),
        ("A1",),
        corrected=False,
    )

    assert result.points[0].value == 0.3
    assert result.points[0].correction_applied is False
    assert result.correction_requested is False
    assert result.issues == ()


def test_plot_label_can_use_standard_or_custom_well_metadata() -> None:
    snapshot = growth_snapshot()
    well = dict(snapshot.wells[0], strain="strain-x", custom_json='{"Batch":"B7"}')
    snapshot = PlateSnapshot(
        snapshot.plate_id,
        snapshot.metadata,
        (well,),
        snapshot.raw_observations,
        snapshot.revisions,
    )

    strain = PrepareGrowthPlotDataService().execute(
        snapshot, (), ("A1",), corrected=False, label_field="strain"
    )
    custom = PrepareGrowthPlotDataService().execute(
        snapshot, (), ("A1",), corrected=False, label_field="custom:Batch"
    )

    assert strain.points[0].label == "strain-x"
    assert custom.points[0].label == "B7"


def test_empty_selected_plot_label_falls_back_to_display_name() -> None:
    result = PrepareGrowthPlotDataService().execute(
        growth_snapshot(), (), ("A1",), corrected=False, label_field="strain"
    )

    assert result.points[0].label == "sample A1"


def test_plot_label_combines_standard_and_custom_metadata_in_selected_order() -> None:
    snapshot = growth_snapshot()
    well = dict(
        snapshot.wells[0],
        strain="strain-x",
        treatment="drug-y",
        concentration=0.125,
        concentration_unit="ug/mL",
        inoculum_size=5,
        inoculum_unit="log CFU/mL",
        custom_json='{"Batch":"B7"}',
    )
    snapshot = PlateSnapshot(
        snapshot.plate_id,
        snapshot.metadata,
        (well,),
        snapshot.raw_observations,
        snapshot.revisions,
    )

    result = PrepareGrowthPlotDataService().execute(
        snapshot,
        (),
        ("A1",),
        corrected=False,
        label_options=GrowthPlotLabelOptions(
            (
                "strain",
                "custom:Batch",
                "treatment",
                "concentration",
                "concentration_unit",
                "inoculum_size",
                "inoculum_unit",
            ),
            separator=" | ",
            prefix="[",
            suffix="]",
        ),
    )

    assert result.points[0].label == ("[strain-x | B7 | drug-y | 0.125 | ug/mL | 5 | log CFU/mL]")


def test_combined_plot_label_can_keep_or_omit_empty_fields() -> None:
    omitted = PrepareGrowthPlotDataService().execute(
        growth_snapshot(),
        (),
        ("A1",),
        corrected=False,
        label_options=GrowthPlotLabelOptions(
            ("display_name", "strain", "raw_label"), separator="_"
        ),
    )
    retained = PrepareGrowthPlotDataService().execute(
        growth_snapshot(),
        (),
        ("A1",),
        corrected=False,
        label_options=GrowthPlotLabelOptions(
            ("display_name", "strain", "raw_label"),
            separator="_",
            omit_empty=False,
        ),
    )

    assert omitted.points[0].label == "sample A1_raw A1"
    assert retained.points[0].label == "sample A1__raw A1"


def test_combined_plot_label_requires_distinct_nonempty_fields() -> None:
    with pytest.raises(ValueError, match="at least one"):
        GrowthPlotLabelOptions(())
    with pytest.raises(ValueError, match="cannot be empty"):
        GrowthPlotLabelOptions(("display_name", " "))
    with pytest.raises(ValueError, match="cannot be repeated"):
        GrowthPlotLabelOptions(("display_name", "display_name"))


def test_curve_label_fields_always_include_the_complete_layout_metadata_set() -> None:
    fields = {field.key: field.label for field in growth_plot_label_fields(growth_snapshot().wells)}

    assert fields == {
        "position": "Well position",
        "raw_label": "Raw label",
        "display_name": "Display name",
        "is_blank": "Blank",
        "background_group": "Background group",
        "grouping_label": "Group",
        "medium": "Media",
        "strain": "Strain",
        "inoculum_size": "Inoculum size",
        "inoculum_unit": "Inoculum unit",
        "replicate": "Replicate",
        "notes": "Notes",
        "treatment": "Treatment",
        "concentration": "Concentration",
        "concentration_unit": "Concentration unit",
    }


def growth_snapshot(*, manual_subtraction: float = 0.0) -> PlateSnapshot:
    return PlateSnapshot(
        plate_id=PlateId("growth-plot"),
        metadata={"manual_subtraction": manual_subtraction},
        wells=(
            {
                "well_id": "well-a1",
                "position": "A1",
                "display_name": "sample A1",
                "raw_label": "raw A1",
                "strain": None,
                "is_blank": 0,
                "background_group": "plate",
            },
        ),
        raw_observations=(
            {
                "well_id": "well-a1",
                "time_index": 0,
                "elapsed_microseconds": 0,
                "channel": "od600",
                "value_raw": 0.3,
            },
        ),
        revisions=(),
    )


def background_row(*, mean: float) -> dict[str, object]:
    return {
        "background_group": "plate",
        "channel": "od600",
        "time_index": 0,
        "elapsed_microseconds": 0,
        "mean_value": mean,
        "std_value": 0.01,
        "coefficient_of_variation": 0.1,
        "blank_count": 2,
        "qc_status": "high_cv",
    }
