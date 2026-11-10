"""Names used for the additional agents in a Growth checkerboard layout."""

from collections.abc import Mapping
from functools import lru_cache

ADDITIONAL_TREATMENT_COLUMNS = {
    f"{label} {slot}": f"{field}_{slot}"
    for slot in (2, 3)
    for label, field in (
        ("Treatment", "treatment"),
        ("Concentration", "conc"),
        ("Concentration unit", "unit"),
    )
}

# Match complete column names only. An unnumbered drug name is user metadata
# and cannot identify a treatment slot unambiguously.
_SEPARATORS = str.maketrans("", "", " _-")
_ALIASES: dict[str, str] = {}
for _slot, _ordinal, _word in ((2, "2nd", "second"), (3, "3rd", "third")):
    for _field, _names in (
        ("treatment", ("treatment", "drug", "antibiotic")),
        ("conc", ("concentration", "conc", "dose")),
        ("unit", ("concentrationunit", "unit")),
    ):
        for _name in _names:
            for _spelling in (f"{_name}{_slot}", f"{_ordinal}{_name}", f"{_word}{_name}"):
                _ALIASES[_spelling] = f"{_field}_{_slot}"
    for _agent in ("drug", "treatment", "antibiotic"):
        for _suffix, _field in (
            ("concentration", "conc"),
            ("conc", "conc"),
            ("unit", "unit"),
            ("concentrationunit", "unit"),
        ):
            for _prefix in (f"{_agent}{_slot}", f"{_ordinal}{_agent}", f"{_word}{_agent}"):
                _ALIASES[_prefix + _suffix] = f"{_field}_{_slot}"


@lru_cache(maxsize=512)
def additional_treatment_key(name: str) -> str | None:
    """Map a recognized custom column spelling to its stored numbered key."""
    return _ALIASES.get(name.strip().casefold().translate(_SEPARATORS))


def additional_treatment_aliases(key: str) -> tuple[str, ...]:
    """Normalized key spellings for the database's metadata-only treatment search."""
    return tuple(alias for alias, field in _ALIASES.items() if field == key)


def additional_treatment_value(custom: Mapping[str, object], key: str) -> object | None:
    """Prefer the stored key, including an explicit clear, then a populated alias."""
    if key in custom:
        return custom[key]
    matches = (
        name
        for name, value in custom.items()
        if value is not None and value != "" and additional_treatment_key(name) == key
    )
    name = min(matches, default=None)
    return custom[name] if name is not None else None
