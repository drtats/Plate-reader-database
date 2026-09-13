"""Growth-unit spelling normalization without concentration conversions."""

from __future__ import annotations

import pytest

from plate_reader.domain.growth.units import normalize_growth_unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("  μg/mL  ", "ug/mL"),
        ("µg/mL", "ug/mL"),
        ("Œºg/mL", "ug/mL"),
        ("Âµg/mL", "ug/mL"),
        ("Î¼g/mL", "ug/mL"),
        ("uM", "uM"),
        ("mM", "mM"),
        ("mg/mL", "mg/mL"),
        (7, "7"),
    ],
)
def test_normalize_growth_unit_known_micro_spellings_only(value: object, expected: str) -> None:
    assert normalize_growth_unit(value) == expected
