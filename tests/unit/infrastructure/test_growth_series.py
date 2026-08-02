from __future__ import annotations

import pytest

from plate_reader.infrastructure.database.growth_series import (
    GrowthSeriesCodecError,
    decode_growth_series,
    encode_growth_series,
)


def test_growth_series_round_trip_is_lossless_and_nullable() -> None:
    rows = [
        {
            "well_id": well_id,
            "channel": "od600",
            "time_index": time_index,
            "elapsed_microseconds": time_index * 600_000_000,
            "value_raw": value,
        }
        for well_id, values in (("well-a1", (0.05, None)), ("well-a2", (0.07, 0.08)))
        for time_index, value in enumerate(values)
    ]

    (chunk,) = encode_growth_series("plate-1", rows, {"well-a1": "A1", "well-a2": "A2"})
    decoded = decode_growth_series(chunk, {"A1": "well-a1", "A2": "well-a2"})

    assert chunk["timepoint_count"] == 2
    assert chunk["position_count"] == 2
    assert [row["value_raw"] for row in decoded] == [0.05, None, 0.07, 0.08]


def test_growth_series_rejects_nonrectangular_input() -> None:
    rows = [
        {
            "well_id": "well-a1",
            "channel": "od600",
            "time_index": 0,
            "elapsed_microseconds": 0,
            "value_raw": 0.05,
        },
        {
            "well_id": "well-a2",
            "channel": "od600",
            "time_index": 1,
            "elapsed_microseconds": 1,
            "value_raw": 0.06,
        },
    ]

    with pytest.raises(GrowthSeriesCodecError, match="shared time axis"):
        encode_growth_series("plate-1", rows, {"well-a1": "A1", "well-a2": "A2"})


def test_growth_series_detects_tampering() -> None:
    rows = [
        {
            "well_id": "well-a1",
            "channel": "od600",
            "time_index": 0,
            "elapsed_microseconds": 0,
            "value_raw": 0.05,
        }
    ]
    (chunk,) = encode_growth_series("plate-1", rows, {"well-a1": "A1"})
    damaged = dict(chunk)
    damaged["content_sha256"] = "0" * 64

    with pytest.raises(GrowthSeriesCodecError, match="checksum"):
        decode_growth_series(damaged, {"A1": "well-a1"})
