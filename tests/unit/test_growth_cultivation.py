from __future__ import annotations

import math

import pytest

from plate_reader.application.contracts import AssayType, PlateId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_cultivation import (
    CultivationAssignment,
    json_object,
    preview_cultivations,
)
from plate_reader.domain.common import DomainValidationError, IssueCode
from plate_reader.domain.growth.cultivation import generate_cultivation_id


def test_generate_cultivation_id_matches_manual_run_examples() -> None:
    assert generate_cultivation_id("PN", "11_J3", "BRV", "2", 1) == ("PN-EXP-11_J3-BRV002R1")
    assert generate_cultivation_id("PN", "MG1655", "MP96A", "23", 2) == ("PN-EXP-MG1655-MP96A023R2")
    assert generate_cultivation_id("TEAM", "strain_variant", "S2Y4A", "1234", 3) == (
        "TEAM-EXP-strain_variant-S2Y4A1234R3"
    )


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
