from __future__ import annotations

import pytest

from plate_reader.domain.common import PLATE_96, DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.common.errors import DomainIssue
from plate_reader.domain.common.plate import PlateGeometry


def test_96_well_geometry_is_row_major_and_canonical() -> None:
    positions = PLATE_96.positions()
    assert len(positions) == 96
    assert positions[0].label == "A1"
    assert positions[11].label == "A12"
    assert positions[12].label == "B1"
    assert positions[-1].label == "H12"
    assert WellPosition.parse(" a01 ").label == "A1"


@pytest.mark.parametrize("value", ["", "A0", "A13", "I1", "AA1", "not-a-well"])
def test_invalid_wells_have_stable_error_code(value: str) -> None:
    with pytest.raises(DomainValidationError) as captured:
        WellPosition.parse(value)
    assert captured.value.primary_code is IssueCode.INVALID_WELL
    assert captured.value.issues[0].severity == "error"


@pytest.mark.parametrize(("rows", "columns"), [(0, 12), (27, 1), (8, 0)])
def test_invalid_plate_geometry_is_rejected(rows: int, columns: int) -> None:
    with pytest.raises(DomainValidationError, match="invalid_plate_format"):
        PlateGeometry(rows, columns)


def test_domain_issue_context_is_deterministically_sorted() -> None:
    issue = DomainIssue.warning(IssueCode.MISSING_WELLS, "missing", z=2, a=1)
    assert issue.context == (("a", "1"), ("z", "2"))


def test_validation_error_requires_an_issue() -> None:
    with pytest.raises(ValueError, match="requires at least one issue"):
        DomainValidationError()
