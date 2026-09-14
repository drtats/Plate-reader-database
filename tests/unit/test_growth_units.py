"""Growth-unit spelling normalization without concentration conversions."""

from __future__ import annotations

import pytest

from plate_reader.domain.growth.units import normalize_growth_label, normalize_growth_unit


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


@pytest.mark.parametrize("spelling", ("u", "μ", "µ", "Œº", "Âµ", "Î¼"))
def test_normalize_growth_label_repairs_only_supplied_unit(spelling: str) -> None:
    label = f"  μstrain_Sulfadiazine_384_{spelling}g/mL_MOPS  "

    assert normalize_growth_label(label, units=("ug/mL",)) == (
        "  μstrain_Sulfadiazine_384_ug/mL_MOPS  "
    )


def test_normalize_growth_label_preserves_other_text_and_requires_unit_context() -> None:
    label = "μstrain_Œºmarker_Œºg/mLextra_Œºg/mL_μM"

    assert normalize_growth_label(label, units=("ug/mL",)) == (
        "μstrain_Œºmarker_Œºg/mLextra_ug/mL_μM"
    )
    assert normalize_growth_label(label, units=()) == label
