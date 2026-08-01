from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from plate_reader.domain.common import DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.growth import (
    GROWTH_BACKGROUND_VERSION,
    GROWTH_NORMALIZATION_VERSION,
    GrowthMeasurement,
    NormalizationSettings,
    WellBackgroundAssignment,
    calculate_backgrounds,
    parse_growth_csv,
    parse_label_layout,
    subtract_background,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures"


def test_growth_normalization_matches_legacy_goldens() -> None:
    with_time = parse_growth_csv(read_text("growth/with_time.csv"))
    without_time = parse_growth_csv(
        read_text("growth/without_time.csv"),
        NormalizationSettings(t0_offset_minutes=5, interval_minutes=10),
    )
    assert growth_records(with_time.measurements) == read_json(
        "golden/growth_normalized_with_time.json"
    )
    assert growth_records(without_time.measurements) == read_json(
        "golden/growth_normalized_without_time.json"
    )
    assert with_time.algorithm_version == GROWTH_NORMALIZATION_VERSION
    assert len(with_time.positions) == 96
    assert not with_time.issues


def test_label_layout_is_anchored_to_physical_wells() -> None:
    labels = parse_label_layout(read_text("growth/labels.csv"))
    assert len(labels) == 96
    assert (labels[0].position.label, labels[0].label) == ("A1", "sample_A1")
    assert (labels[11].position.label, labels[11].label) == ("A12", "sample_A12")
    assert (labels[12].position.label, labels[12].label) == ("B1", "sample_B1")


@pytest.mark.parametrize(
    ("csv_text", "code"),
    [
        ("", IssueCode.EMPTY_INPUT),
        ("Time,A1\n", IssueCode.EMPTY_INPUT),
        ("Time,A1\n0,1,2\n", IssueCode.INVALID_VALUE),
        ("Time,time,A1\n0,0,1\n", IssueCode.DUPLICATE_TIME),
        ("Time,I1\n0,1\n", IssueCode.INVALID_WELL),
        ("Time,A1,a1\n0,1,1\n", IssueCode.DUPLICATE_WELL),
        ("Time,temperature\n0,37\n", IssueCode.INVALID_WELL),
        ("Time,A1\n0,NaN\n", IssueCode.INVALID_VALUE),
        ("Time,A1\nnot-a-time,0.1\n", IssueCode.INVALID_TIME),
        ("Time,A1\nNaN,0.1\n", IssueCode.INVALID_TIME),
        ("Time,A1\n-1,0.1\n", IssueCode.NEGATIVE_TIME),
        ("Time,A1\n0,0.1\n0,0.2\n", IssueCode.DUPLICATE_TIME),
        ('"unterminated', IssueCode.INVALID_VALUE),
    ],
)
def test_growth_csv_failures_have_stable_codes(csv_text: str, code: IssueCode) -> None:
    with pytest.raises(DomainValidationError) as captured:
        parse_growth_csv(csv_text)
    assert captured.value.primary_code is code


def test_partial_plate_and_unknown_column_are_reported() -> None:
    result = parse_growth_csv("Time,A1,note\n0,0.1,ignored\n")
    assert [issue.code for issue in result.issues] == [
        IssueCode.UNKNOWN_COLUMN,
        IssueCode.MISSING_WELLS,
    ]
    assert len(result.measurements) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"t0_offset_minutes": -1},
        {"t0_offset_minutes": math.inf},
        {"interval_minutes": 0},
        {"interval_minutes": math.nan},
        {"channel": "  "},
    ],
)
def test_invalid_normalization_settings_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        NormalizationSettings(**kwargs)  # type: ignore[arg-type]


def test_invalid_layout_shape_is_rejected() -> None:
    with pytest.raises(DomainValidationError) as captured:
        parse_label_layout("a,b\nc,d\n")
    assert captured.value.primary_code is IssueCode.INVALID_LAYOUT


def test_backgrounds_match_legacy_and_add_explicit_qc() -> None:
    normalized = parse_growth_csv(read_text("growth/with_time.csv"))
    assignments = load_growth_assignments()
    result = calculate_backgrounds(normalized.measurements, assignments)
    legacy = read_json("golden/growth_backgrounds.json")
    actual = [
        {
            "bg_group": row.background_group,
            "time_min": row.elapsed_microseconds / 60_000_000,
            "signal_type": row.channel,
            "bg_mean": row.mean_value,
            "bg_std": row.std_value,
            "bg_cv": row.coefficient_of_variation,
            "n_blank_wells": row.blank_count,
        }
        for row in result.backgrounds
    ]
    expected = [{key: value for key, value in row.items() if key != "run_id"} for row in legacy]
    assert len(actual) == len(expected) == 8
    for actual_row, expected_row in zip(actual, expected, strict=True):
        assert actual_row.keys() == expected_row.keys()
        for key, value in actual_row.items():
            if isinstance(value, float):
                assert value == pytest.approx(expected_row[key], abs=1e-12)
            else:
                assert value == expected_row[key]
    assert result.algorithm_version == GROWTH_BACKGROUND_VERSION
    assert {issue.code for issue in result.issues} == {
        IssueCode.MISSING_BACKGROUND,
        IssueCode.HIGH_BACKGROUND_CV,
    }


def test_missing_background_is_not_silently_labeled_corrected() -> None:
    normalized = parse_growth_csv(read_text("growth/with_time.csv"))
    assignments = load_growth_assignments()
    backgrounds = calculate_backgrounds(normalized.measurements, assignments)
    corrected = subtract_background(normalized.measurements, assignments, backgrounds.backgrounds)
    c1 = [row for row in corrected.measurements if row.measurement.position.label == "C1"]
    assert c1
    assert all(row.background_mean is None and row.corrected_value is None for row in c1)
    assert IssueCode.MISSING_BACKGROUND in {issue.code for issue in corrected.issues}


def test_single_blank_and_manual_offset_are_deterministic() -> None:
    a1 = WellPosition.parse("A1")
    a2 = WellPosition.parse("A2")
    measurements = (
        GrowthMeasurement(a1, 0, 0, "od600", 0.05),
        GrowthMeasurement(a2, 0, 0, "od600", 0.20),
    )
    assignments = (
        WellBackgroundAssignment(a1, True, "plate"),
        WellBackgroundAssignment(a2, False, "plate"),
    )
    backgrounds = calculate_backgrounds(measurements, assignments)
    assert backgrounds.backgrounds[0].std_value == 0
    assert IssueCode.INSUFFICIENT_BLANKS in {issue.code for issue in backgrounds.issues}
    corrected = subtract_background(
        measurements, assignments, backgrounds.backgrounds, manual_offset=0.01
    )
    a2_result = next(row for row in corrected.measurements if row.measurement.position == a2)
    assert a2_result.corrected_value == pytest.approx(0.14)


def test_background_input_validation_and_missing_blanks() -> None:
    a1 = WellPosition.parse("A1")
    measurement = GrowthMeasurement(a1, 0, 0, "od600", 0.1)
    assignment = WellBackgroundAssignment(a1, False, "plate")
    no_blanks = calculate_backgrounds((measurement,), (assignment,))
    assert {issue.code for issue in no_blanks.issues} == {
        IssueCode.MISSING_BLANKS,
        IssueCode.MISSING_BACKGROUND,
    }
    with pytest.raises(DomainValidationError, match="duplicate_well"):
        calculate_backgrounds((measurement,), (assignment, assignment))
    with pytest.raises(DomainValidationError):
        subtract_background((measurement,), (assignment,), (), manual_offset=math.nan)
    with pytest.raises(DomainValidationError):
        WellBackgroundAssignment(a1, False, "  ")
    with pytest.raises(DomainValidationError):
        GrowthMeasurement(a1, -1, 0, "od600", 0.1)
    with pytest.raises(DomainValidationError):
        GrowthMeasurement(a1, 0, 0, "od600", math.nan)
    with pytest.raises(DomainValidationError):
        GrowthMeasurement(a1, 0, 0, " ", 0.1)


def growth_records(measurements: tuple[GrowthMeasurement, ...]) -> object:
    records = [
        {
            "signal_type": measurement.channel,
            "time_min": measurement.elapsed_minutes,
            "value_raw": measurement.value_raw,
            "well": measurement.position.label,
        }
        for measurement in measurements
    ]
    return sorted(records, key=lambda row: (row["time_min"], row["well"]))


def load_growth_assignments() -> tuple[WellBackgroundAssignment, ...]:
    with (FIXTURES / "growth" / "well_metadata.csv").open(encoding="utf-8") as handle:
        return tuple(
            WellBackgroundAssignment(
                WellPosition.parse(row["well"]),
                row["is_blank"] == "True",
                row["bg_group"],
            )
            for row in csv.DictReader(handle)
        )


def read_text(relative_path: str) -> str:
    return (FIXTURES / relative_path).read_text(encoding="utf-8")


def read_json(relative_path: str) -> object:
    return json.loads(read_text(relative_path))
