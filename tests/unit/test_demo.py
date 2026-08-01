from __future__ import annotations

import pytest

from plate_reader.application.demo import synthetic_growth_csv
from plate_reader.domain.growth import parse_growth_csv


def test_synthetic_growth_csv_is_complete_and_deterministic() -> None:
    first = synthetic_growth_csv()
    second = synthetic_growth_csv()
    normalized = parse_growth_csv(first)

    assert first == second
    assert len(normalized.measurements) == 13_920
    assert len(normalized.positions) == 96
    assert len(normalized.timepoints_microseconds) == 145
    assert normalized.timepoints_microseconds[0] == 0
    assert normalized.timepoints_microseconds[-1] == 24 * 60 * 60_000_000


@pytest.mark.parametrize(
    ("duration", "interval"),
    ((0, 10), (60, 0), (60, 7)),
)
def test_synthetic_growth_csv_rejects_invalid_timing(duration: int, interval: int) -> None:
    with pytest.raises(ValueError):
        synthetic_growth_csv(duration_minutes=duration, interval_minutes=interval)
