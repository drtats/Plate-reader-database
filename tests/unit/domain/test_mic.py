from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from plate_reader.domain.common import DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.mic import (
    MIC_ENDPOINT_VERSION,
    MIC_PLATE_PARSER_VERSION,
    MicOperator,
    MicWell,
    analyze_mic_endpoint,
    parse_mic_plate_csv,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures"


def test_mic_fixture_matches_legacy_scientific_behavior() -> None:
    wells = load_mic_fixture()
    result = analyze_mic_endpoint(wells, threshold=0.1)
    golden = read_json("golden/mic_endpoint.json")
    assert result.background_value == pytest.approx(golden["background_value"])
    assert result.threshold == golden["threshold"]
    assert result.algorithm_version == MIC_ENDPOINT_VERSION

    actual_by_strain = {row.strain: row for row in result.results}
    expected_by_strain = {row["strain"]: row for row in golden["results"]}
    assert actual_by_strain.keys() == expected_by_strain.keys()
    for strain, actual in actual_by_strain.items():
        expected = expected_by_strain[strain]
        assert actual.treatment == expected["antibiotic"]
        assert actual.medium == expected["media"]
        assert actual.replicate == expected["replicate"]
        assert actual.mic_value == expected["mic_value"]
        assert actual.mic_operator == expected["mic_operator"]
        assert actual.mic_unit == expected["mic_unit"]
        assert actual.lowest_tested_concentration == expected["lowest_tested_conc"]
        assert actual.highest_tested_concentration == expected["highest_tested_conc"]
        assert actual.concentrations == tuple(expected["concentration_values"])
        assert actual.point_count == expected["num_points"]
        warning = actual.issues[0].message if actual.issues else None
        assert warning == expected["warning"]
        # The legacy calculator returned 0 here and the UI patched it afterward.
        assert actual.threshold_used == 0.1

    actual_calls = {
        call.position.label: call for call in result.well_calls if call.position.row_index < 4
    }
    for expected in golden["well_calls"]:
        actual = actual_calls[expected["well_position"]]
        assert actual.value_background_subtracted == pytest.approx(expected["od_bg_subtracted"])
        assert actual.growth_call is expected["growth_call"]


def test_duplicate_concentrations_use_any_growth() -> None:
    wells = (
        mic_well("A1", 0.2, 0.5),
        mic_well("A2", 0.0, 0.5),
        mic_well("A3", 0.0, 1.0),
        MicWell(WellPosition.parse("H12"), 0.0, is_blank=True),
    )
    result = analyze_mic_endpoint(wells, threshold=0.1).results[0]
    assert result.concentrations == (0.5, 1.0)
    assert result.point_count == 3
    assert result.mic_value == 1.0
    assert result.mic_operator is MicOperator.EQUAL


def test_all_growth_all_no_growth_and_bounce_operators() -> None:
    results = {row.strain: row for row in analyze_mic_endpoint(load_mic_fixture(), 0.1).results}
    assert results["strain_all_growth"].mic_operator is MicOperator.GREATER_THAN
    assert results["strain_all_no_growth"].mic_operator is MicOperator.LESS_THAN_OR_EQUAL
    bounce = results["strain_bounce"]
    assert bounce.mic_operator is MicOperator.EQUAL
    assert bounce.issues[0].code is IssueCode.GROWTH_BOUNCE


def test_missing_blanks_labels_and_concentration_are_explicit() -> None:
    wells = (
        MicWell(
            WellPosition.parse("A1"),
            0.2,
            concentration=1.0,
            strain=" ",
            treatment=None,
            medium="",
        ),
        MicWell(
            WellPosition.parse("A2"),
            0.2,
            concentration=None,
            strain="strain",
            treatment="drug",
            medium="medium",
        ),
    )
    result = analyze_mic_endpoint(wells, threshold=0.1)
    codes = [issue.code for issue in result.issues]
    assert codes.count(IssueCode.MISSING_BLANKS) == 1
    assert codes.count(IssueCode.MISSING_GROUP_LABEL) == 3
    assert codes.count(IssueCode.INVALID_CONCENTRATION) == 1
    assert result.results[0].strain == "Unknown"
    assert result.results[0].treatment == "Unknown"
    assert result.results[0].medium == "Unknown"


def test_no_valid_group_is_reported() -> None:
    result = analyze_mic_endpoint(
        (MicWell(WellPosition.parse("A1"), 0.05, is_blank=True),), threshold=0.1
    )
    assert result.background_value == 0.05
    assert [issue.code for issue in result.issues] == [IssueCode.EMPTY_MIC_GROUP]


@pytest.mark.parametrize("threshold", [-1.0, math.nan, math.inf])
def test_invalid_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(DomainValidationError) as captured:
        analyze_mic_endpoint((mic_well("A1", 0.2, 1.0),), threshold)
    assert captured.value.primary_code is IssueCode.INVALID_THRESHOLD


def test_mic_input_validation() -> None:
    with pytest.raises(DomainValidationError, match="empty_input"):
        analyze_mic_endpoint((), 0.1)
    well = mic_well("A1", 0.2, 1.0)
    with pytest.raises(DomainValidationError, match="duplicate_well"):
        analyze_mic_endpoint((well, well), 0.1)
    with pytest.raises(DomainValidationError):
        MicWell(WellPosition.parse("A1"), math.nan)
    with pytest.raises(DomainValidationError):
        MicWell(WellPosition.parse("A1"), 0.1, concentration=-1)
    with pytest.raises(DomainValidationError):
        MicWell(WellPosition.parse("A1"), 0.1, replicate=0)
    with pytest.raises(DomainValidationError):
        MicWell(WellPosition.parse("A1"), 0.1, concentration_unit=" ")


def test_mic_analysis_is_deterministic() -> None:
    wells = load_mic_fixture()
    assert analyze_mic_endpoint(wells, 0.1) == analyze_mic_endpoint(wells, 0.1)


def test_long_csv_parser_supports_aliases_and_custom_labels() -> None:
    wells = parse_mic_plate_csv(
        "well,od,blank,strain,antibiotic,concentration,media,replicate,Oxygen\n"
        "A1,0.25,no,S1,Drug,1,M9,2,Aerobic\n"
        "H12,0.05,yes,,,,M9,,Anaerobic\n"
    )

    assert MIC_PLATE_PARSER_VERSION == "mic-long-csv/1.0.0"
    assert wells[0] == MicWell(
        position=WellPosition.parse("A1"),
        value_raw=0.25,
        strain="S1",
        treatment="Drug",
        concentration=1.0,
        medium="M9",
        replicate=2,
        custom_labels=(("oxygen", "Aerobic"),),
    )
    assert wells[1].position.label == "H12"
    assert wells[1].is_blank is True
    assert wells[1].replicate == 1


@pytest.mark.parametrize(
    "text",
    (
        "",
        "well_position,strain\nA1,S1\n",
        "well_position,od_raw\nA1,not-a-number\n",
        "well_position,od_raw,is_blank\nA1,0.1,maybe\n",
        "well_position,od_raw,replicate\nA1,0.1,0\n",
        "well_position,od_raw\nA1,0.1\nA1,0.2\n",
    ),
)
def test_long_csv_parser_rejects_invalid_input(text: str) -> None:
    with pytest.raises(DomainValidationError):
        parse_mic_plate_csv(text)


@pytest.mark.parametrize(
    "text",
    (
        "well_position,well,od_raw\nA1,A1,0.1\n",
        "well_position,od_raw\n",
        "well_position,od_raw\n,0.1\n",
        "well_position,od_raw\nA1,nan\n",
        "well_position,od_raw,replicate\nA1,0.1,one\n",
        "well_position,od_raw\nA1,0.1,extra\n",
    ),
)
def test_long_csv_parser_rejects_structural_and_numeric_edge_cases(text: str) -> None:
    with pytest.raises(DomainValidationError):
        parse_mic_plate_csv(text)


@pytest.mark.parametrize("value", ("true", "1", "yes", "y"))
def test_long_csv_parser_accepts_true_boolean_spellings(value: str) -> None:
    parsed = parse_mic_plate_csv(f"well_position,od_raw,is_blank\nA1,0.1,{value}\n")
    assert parsed[0].is_blank is True


@pytest.mark.parametrize("value", ("false", "0", "no", "n"))
def test_long_csv_parser_accepts_false_boolean_spellings(value: str) -> None:
    parsed = parse_mic_plate_csv(f"well_position,od_raw,is_blank\nA1,0.1,{value}\n")
    assert parsed[0].is_blank is False


def test_long_csv_parser_skips_blank_rows_and_handles_bom() -> None:
    parsed = parse_mic_plate_csv("\ufeffWell-Position,OD\n\nA1,0.1\n")
    assert tuple(well.position.label for well in parsed) == ("A1",)


def test_custom_label_names_are_validated() -> None:
    with pytest.raises(DomainValidationError):
        MicWell(WellPosition.parse("A1"), 0.1, custom_labels=(("", "value"),))
    with pytest.raises(DomainValidationError):
        MicWell(
            WellPosition.parse("A1"),
            0.1,
            custom_labels=(("label", "a"), ("label", "b")),
        )


def mic_well(position: str, value: float, concentration: float) -> MicWell:
    return MicWell(
        position=WellPosition.parse(position),
        value_raw=value,
        strain="strain",
        treatment="compound",
        concentration=concentration,
        medium="medium",
        replicate=1,
    )


def load_mic_fixture() -> tuple[MicWell, ...]:
    with (FIXTURES / "mic" / "plate_cases.csv").open(encoding="utf-8") as handle:
        rows = tuple(csv.DictReader(handle))
    return tuple(
        MicWell(
            position=WellPosition.parse(row["well_position"]),
            value_raw=float(row["od_raw"]),
            is_blank=row["is_blank"] == "True",
            strain=row["strain"] or None,
            treatment=row["antibiotic"] or None,
            concentration=float(row["concentration"]) if row["concentration"] else None,
            concentration_unit=row["concentration_unit"],
            medium=row["media"] or None,
            replicate=int(row["replicate"]),
        )
        for row in rows
    )


def read_json(relative_path: str) -> object:
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))
