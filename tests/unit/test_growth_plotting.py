from __future__ import annotations

import pytest

from plate_reader.application.contracts import PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_plotting import PrepareGrowthPlotDataService
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
