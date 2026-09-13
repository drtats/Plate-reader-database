from __future__ import annotations

import math
from typing import cast

import pytest

from plate_reader.application.contracts import AssayType, PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_cultivation import (
    CultivationAssignment,
    GrowthCultivationRepository,
    json_object,
    preview_cultivations,
    suggested_cultivation_experiment_code,
)
from plate_reader.domain.common import DomainValidationError, IssueCode
from plate_reader.domain.growth.cultivation import (
    DEFAULT_CULTIVATION_PATTERN,
    LEGACY_CULTIVATION_PATTERN,
    format_cultivation_id,
    generate_cultivation_id,
)


def test_generate_cultivation_id_matches_manual_run_examples() -> None:
    assert generate_cultivation_id("PN", "11_J3", "BRV", "2", 1) == ("PN-EXP-11_J3-BRV002R1")
    assert generate_cultivation_id("PN", "MG1655", "MP96A", "23", 2) == ("PN-EXP-MG1655-MP96A023R2")
    assert generate_cultivation_id("TEAM", "strain_variant", "S2Y4A", "1234", 3) == (
        "TEAM-EXP-strain_variant-S2Y4A1234R3"
    )


def _format(pattern: str, **changes: object) -> str:
    components: dict[str, object] = {
        "team_code": "PN",
        "strain": "J3",
        "system_code": "BRV",
        "cultivation_run": "2",
        "replicate": 1,
        "experiment_code": "001",
        "position": "a1",
    }
    components.update(changes)
    return format_cultivation_id(pattern, **components)  # type: ignore[arg-type]


def test_default_pattern_distinguishes_wells_and_replicates() -> None:
    assert _format(DEFAULT_CULTIVATION_PATTERN) == "PN-EXP-J3-001-A01-R1"
    assert _format(DEFAULT_CULTIVATION_PATTERN, position="A2") != _format(
        DEFAULT_CULTIVATION_PATTERN
    )
    assert _format(DEFAULT_CULTIVATION_PATTERN, replicate=2) != _format(DEFAULT_CULTIVATION_PATTERN)


def test_legacy_pattern_matches_existing_generator_byte_for_byte() -> None:
    assert _format(LEGACY_CULTIVATION_PATTERN) == generate_cultivation_id("PN", "J3", "BRV", "2", 1)


@pytest.mark.parametrize(
    "pattern",
    [
        "literal",
        "{unknown}",
        "{team.__class__}",
        "{team[0]}",
        "{team!r}",
        "{team:}",
        "{run:03d}",
        "{team",
        "{team}/unsafe",
        "{team}" + "a" * 256,
    ],
)
def test_formatter_rejects_unsafe_patterns_and_outputs(pattern: str) -> None:
    with pytest.raises(DomainValidationError):
        _format(pattern)


def test_formatter_requires_only_referenced_components() -> None:
    assert _format("{team}-{well}", system_code="", cultivation_run="", experiment_code="") == (
        "PN-A01"
    )
    with pytest.raises(DomainValidationError, match="experiment"):
        _format(DEFAULT_CULTIVATION_PATTERN, experiment_code="")
    with pytest.raises(DomainValidationError, match="well"):
        _format("{well}", position="H13")
    with pytest.raises(DomainValidationError, match="cultivation_run"):
        _format("{run}", cultivation_run="0")


@pytest.mark.parametrize("code", ["", "0", "000", "custom", "1.0"])
def test_default_pattern_rejects_nonpositive_or_nonnumeric_experiment_code(code: str) -> None:
    with pytest.raises(DomainValidationError, match="experiment_code"):
        _format(DEFAULT_CULTIVATION_PATTERN, experiment_code=code)


def test_default_pattern_normalizes_experiment_code_but_custom_pattern_does_not() -> None:
    assert _format(DEFAULT_CULTIVATION_PATTERN, experiment_code="1") == ("PN-EXP-J3-001-A01-R1")
    assert _format("{team}-{experiment}-{well}", experiment_code="custom") == ("PN-custom-A01")


class CodeProjection:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows

    def growth_cultivation_codes(self) -> tuple[dict[str, object], ...]:
        return self.rows


def test_suggested_experiment_code_starts_at_001_and_uses_saved_numeric_max() -> None:
    empty = cast(GrowthCultivationRepository, CodeProjection(()))
    assert suggested_cultivation_experiment_code(empty) == "001"

    projection = cast(
        GrowthCultivationRepository,
        CodeProjection(
            (
                {"plate_id": "plate-1", "custom_json": {"CultivationExperimentCode": "007"}},
                {"plate_id": "plate-2", "custom_json": '{"CultivationExperimentCode":"0010"}'},
                {"plate_id": "plate-3", "custom_json": {"CultivationExperimentCode": "custom"}},
                {"plate_id": "plate-4", "custom_json": {"CultivationExperimentCode": "000"}},
            )
        ),
    )
    assert suggested_cultivation_experiment_code(projection) == "011"


def test_suggested_experiment_code_expands_beyond_999() -> None:
    projection = cast(
        GrowthCultivationRepository,
        CodeProjection(
            ({"plate_id": "plate-1", "custom_json": {"CultivationExperimentCode": "999"}},)
        ),
    )
    assert suggested_cultivation_experiment_code(projection) == "1000"


@pytest.mark.parametrize(
    ("team", "strain", "system", "run", "replicate"),
    [
        ("", "J3", "BRV", "1", 1),
        ("PN-EXP", "J3", "BRV", "1", 1),
        ("PN", "J3-b", "BRV", "1", 1),
        ("PN", "J3", "BRV-002", "1", 1),
        ("PN", "J3", "BRV002", "1", 1),
        ("PN", "J3", "BRV", "", 1),
        ("PN", "J3", "BRV", "1.5", 1),
        ("PN", "J3", "BRV", "0", 1),
        ("PN", "J3", "BRV", "1", 0),
        ("PN", "J3", "BRV", "1", True),
    ],
)
def test_generate_cultivation_id_rejects_invalid_or_ambiguous_components(
    team: str, strain: str, system: str, run: str, replicate: int
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        generate_cultivation_id(team, strain, system, run, replicate)

    assert raised.value.primary_code is IssueCode.INVALID_VALUE


def test_preview_uses_persisted_strain_and_replicate_and_retains_descriptives() -> None:
    preview = preview_cultivations(
        snapshot(),
        {
            "Team_Code": "PN",
            "CultivationSystemCode": "BRV",
            "Project": "Persister study",
            "InoculationDateTime": "2026-09-12T08:30:00-04:00",
        },
        (CultivationAssignment("a01", "2"),),
    )

    assert preview == (
        {
            "Team_Code": "PN",
            "CultivationSystemCode": "BRV",
            "Project": "Persister study",
            "InoculationDateTime": "2026-09-12T08:30:00-04:00",
            "Well": "A1",
            "Cultivation": "PN-EXP-J3-BRV002R2",
            "Strain": "J3",
            "CultivationRun": "002",
            "Replicate": 2,
        },
    )


def test_preview_allows_no_assignments_for_registry_only_save() -> None:
    assert (
        preview_cultivations(
            snapshot(),
            {"Project": "registry only", "InoculationDateTime": ""},
            (),
        )
        == ()
    )


def test_pattern_preview_uses_saved_per_well_strains_and_replicates() -> None:
    base = snapshot()
    varied = PlateSnapshot(
        base.plate_id,
        base.metadata,
        (
            base.wells[0],
            {"position": "A2", "strain": "MG1655", "replicate": 1, "custom_json": "{}"},
            {"position": "A3", "strain": "J3", "replicate": 2, "custom_json": "{}"},
        ),
        base.raw_observations,
        base.revisions,
    )
    records = preview_cultivations(
        varied,
        {"Team_Code": "PN", "CultivationIDPattern": "shared other default"},
        tuple(
            CultivationAssignment(position, "", DEFAULT_CULTIVATION_PATTERN, "1")
            for position in ("A1", "A2", "A3")
        ),
    )
    assert [record["Cultivation"] for record in records] == [
        "PN-EXP-J3-001-A01-R2",
        "PN-EXP-MG1655-001-A02-R1",
        "PN-EXP-J3-001-A03-R2",
    ]
    assert all(record["CultivationIDPattern"] == DEFAULT_CULTIVATION_PATTERN for record in records)
    assert all(record["CultivationExperimentCode"] == "001" for record in records)
    assert all(record["CultivationRun"] == "" for record in records)


def test_pattern_preview_rejects_duplicate_output_and_unknown_well() -> None:
    base = snapshot()
    varied = PlateSnapshot(
        base.plate_id,
        base.metadata,
        (
            base.wells[0],
            {"position": "A2", "strain": "J3", "replicate": 2, "custom_json": "{}"},
        ),
        base.raw_observations,
        base.revisions,
    )
    with pytest.raises(DomainValidationError, match="Duplicate cultivation ID"):
        preview_cultivations(
            varied,
            {"Team_Code": "PN"},
            (
                CultivationAssignment("A1", "", "{team}-{strain}-R{replicate}"),
                CultivationAssignment("A2", "", "{team}-{strain}-R{replicate}"),
            ),
        )
    with pytest.raises(DomainValidationError, match="unknown well"):
        preview_cultivations(
            varied,
            {"Team_Code": "PN"},
            (CultivationAssignment("H12", "", DEFAULT_CULTIVATION_PATTERN, "001"),),
        )


@pytest.mark.parametrize("run", ["0", "1.5", "abc"])
def test_pattern_preview_rejects_invalid_supplied_run_even_when_unused(run: str) -> None:
    with pytest.raises(DomainValidationError, match="CultivationRun"):
        preview_cultivations(
            snapshot(),
            {"Team_Code": "PN"},
            (CultivationAssignment("A1", run, DEFAULT_CULTIVATION_PATTERN, "001"),),
        )


@pytest.mark.parametrize(
    "assignments",
    [
        (CultivationAssignment("A1", "1"), CultivationAssignment("a01", "2")),
        (CultivationAssignment("H12", "1"),),
        (CultivationAssignment("A2", "1"),),
    ],
)
def test_preview_rejects_duplicate_unknown_or_missing_strain_assignments(
    assignments: tuple[CultivationAssignment, ...],
) -> None:
    with pytest.raises(DomainValidationError):
        preview_cultivations(
            snapshot(),
            {"Team_Code": "TEAM", "CultivationSystemCode": "SYS"},
            assignments,
        )


def test_preview_rejects_duplicate_generated_ids() -> None:
    duplicate_well = {
        "position": "A3",
        "strain": "J3",
        "replicate": 2,
        "custom_json": "{}",
    }
    base = snapshot()
    duplicated = PlateSnapshot(
        base.plate_id,
        base.metadata,
        (*base.wells, duplicate_well),
        base.raw_observations,
        base.revisions,
    )

    with pytest.raises(DomainValidationError, match="Duplicate cultivation ID"):
        preview_cultivations(
            duplicated,
            {"Team_Code": "TEAM", "CultivationSystemCode": "SYS"},
            (CultivationAssignment("A1", "7"), CultivationAssignment("A3", "7")),
        )


@pytest.mark.parametrize(
    "value",
    [[], "[]", {1: "value"}, {"number": math.nan}, {"tuple": (1, 2)}],
)
def test_json_object_rejects_non_object_or_non_json_values(value: object) -> None:
    with pytest.raises(ValueError):
        json_object(value)


def test_json_object_parses_and_copies_strict_objects() -> None:
    source = {"nested": {"items": [1, True, None]}}
    result = json_object(source)
    assert result == source
    assert result is not source
    assert json_object('{"Team_Code":"PN"}') == {"Team_Code": "PN"}


@pytest.mark.parametrize("value", ["2026-09-12", "09/12/2026 08:30", "2026-13-12T08:30:00"])
def test_preview_rejects_malformed_inoculation_datetime(value: str) -> None:
    with pytest.raises(DomainValidationError, match="InoculationDateTime"):
        preview_cultivations(
            snapshot(),
            {
                "Team_Code": "TEAM",
                "CultivationSystemCode": "SYS",
                "InoculationDateTime": value,
            },
            (),
        )


def test_preview_accepts_iso_datetime_with_space_separator() -> None:
    assert (
        preview_cultivations(
            snapshot(),
            {"InoculationDateTime": "2026-03-31 13:39"},
            (),
        )
        == ()
    )


def snapshot() -> PlateSnapshot:
    return PlateSnapshot(
        plate_id=PlateId("plate-1"),
        metadata={"assay_type": AssayType.GROWTH, "plate_custom_json": "{}"},
        wells=(
            {"position": "A1", "strain": "J3", "replicate": 2, "custom_json": "{}"},
            {"position": "A2", "strain": None, "replicate": 1, "custom_json": "{}"},
        ),
        raw_observations=(),
        revisions=(),
    )
