"""Conservative spelling normalization for growth condition units."""

from __future__ import annotations

import re
from collections.abc import Iterable

_MICRO_SPELLINGS = ("Œº", "Âµ", "Î¼", "μ", "µ")
_MICRO_PATTERN = "(?:u|Œº|Âµ|Î¼|μ|µ)"


def normalize_growth_unit(value: object) -> str:
    """Unify known micro symbols/encoding artifacts without converting unit scales.

    Keep every other character, including scientific case, unchanged. Replacement
    of the multi-character encoding artifacts must precede the individual glyphs.
    """

    unit = "" if value is None else str(value).strip()
    for spelling in _MICRO_SPELLINGS:
        unit = unit.replace(spelling, "u")
    return unit


def normalize_growth_label(value: object, *, units: Iterable[object]) -> str:
    """Repair micro spellings only where a supplied unit occurs in a label.

    The unit must occupy a full label component, bounded by separators or the
    string edges. This leaves unrelated strain and treatment text untouched.
    Unlike unit normalization, label normalization preserves surrounding spaces.
    """

    label = "" if value is None else str(value)
    for value_unit in units:
        unit = normalize_growth_unit(value_unit)
        if "u" not in unit:
            continue
        pattern = "".join(
            _MICRO_PATTERN if character == "u" else re.escape(character) for character in unit
        )
        # Unicode letters and numbers cannot adjoin the unit; underscore is a
        # common generated-name separator and is intentionally allowed.
        bounded = re.compile(rf"(?<![^\W_]){pattern}(?![^\W_])")
        label = bounded.sub(unit, label)
    return label
