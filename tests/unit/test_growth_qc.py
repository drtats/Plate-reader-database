from __future__ import annotations

import pytest

from plate_reader.application.services import SummarizeGrowthBackgroundQcService


def test_growth_background_qc_summary_groups_channels_and_statuses() -> None:
    report = SummarizeGrowthBackgroundQcService().execute(
        (
            background("M9", "od600", 0.02, 3, "good"),
            background("M9", "od600", 0.07, 3, "caution"),
            background("M9", "od600", 0.12, 2, "high_cv"),
            background("plate", "od600", 0.01, 4, "good"),
        )
    )

    assert report.total_timepoints == 4
    assert len(report.groups) == 2
    m9 = report.groups[0]
    assert (m9.background_group, m9.channel, m9.timepoint_count) == ("M9", "od600", 3)
    assert (m9.blank_count_min, m9.blank_count_max) == (2, 3)
    assert m9.mean_cv == pytest.approx(0.07)
    assert m9.max_cv == 0.12
    assert (m9.good_count, m9.caution_count, m9.high_cv_count) == (1, 1, 1)


def test_growth_background_qc_summary_handles_empty_and_invalid_rows() -> None:
    service = SummarizeGrowthBackgroundQcService()
    assert service.execute(()).groups == ()
    with pytest.raises(ValueError, match="positive integers"):
        service.execute((background("plate", "od600", 0.1, 0, "high_cv"),))
    with pytest.raises(ValueError, match="not a valid BackgroundQcStatus"):
        service.execute((background("plate", "od600", 0.1, 2, "invalid"),))


def background(
    group: str, channel: str, cv: float, blank_count: int, status: str
) -> dict[str, object]:
    return {
        "background_group": group,
        "channel": channel,
        "coefficient_of_variation": cv,
        "blank_count": blank_count,
        "qc_status": status,
    }
