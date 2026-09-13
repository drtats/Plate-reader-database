"""Conservative spelling normalization for growth condition units."""

from __future__ import annotations


def normalize_growth_unit(value: object) -> str:
    """Unify known micro symbols/encoding artifacts without converting unit scales.

    Keep every other character, including scientific case, unchanged. Replacement
    of the multi-character encoding artifacts must precede the individual glyphs.
    """

    unit = "" if value is None else str(value).strip()
    for spelling in ("Œº", "Âµ", "Î¼", "μ", "µ"):
        unit = unit.replace(spelling, "u")
    return unit
