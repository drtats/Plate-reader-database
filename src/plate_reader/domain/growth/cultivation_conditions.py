"""Pure, stable identity for the conditions under which a growth well was cultivated."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.growth.units import normalize_growth_unit

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_EDITABLE_KEYS = (
    "editable_metadata_json",
    "metadata_json_editable",
    "metadata_editable_json",
    "editable_metadata",
    "metadata_editable",
)
_RESERVED_EXTRA_FIELDS = frozenset(
    name.casefold()
    for name in (
        "Cultivation",
        "CultivationReplicate",
        "CultivationConditionKey",
        "CultivationConditionFields",
        "CultivationReplicateScope",
        "CultivationReplicateMode",
        "CultivationIDPattern",
        "CultivationExperimentCode",
        "CultivationRun",
        "Team_Code",
        "CultivationSystemCode",
        "well_id",
        "plate_id",
        "Well",
        "position",
        "Replicate",
        "LocalReplicate",
        "experiment_date",
        "created_at",
        "primary_condition_overrides",
    )
)


def cultivation_condition_key(
    well: Mapping[str, object],
    metadata: Mapping[str, object],
    scope: str,
    extra_fields: tuple[str, ...] = (),
    *,
    normalize_units: bool = False,
) -> str:
    """Return canonical JSON for cultivation conditions, excluding layout and run identity.

    Unknown strain or medium is deliberately specific to one physical well: two
    incomplete records cannot safely be presumed to describe the same culture.
    Units remain literal by default to preserve saved version-1 fingerprints.
    Export comparisons can opt into micro-spelling equivalence; no concentration
    conversion is made in either mode.
    """

    custom = {
        **_json_object(well.get("condition_custom_json")),
        **_json_object(well.get("custom_json")),
    }
    plate_custom = _json_object(metadata.get("plate_custom_json"))
    experiment_custom = _json_object(metadata.get("experiment_custom_json"))
    additional = _extra_field_names(extra_fields)
    strain = _text(well.get("strain"))
    medium = _text(well.get("medium"))
    identity: dict[str, object] = {
        "version": 1,
        "scope": _text(scope),
        "strain": strain,
        "medium": medium,
        "inoculum_size": _numeric(well.get("inoculum_size")),
        "inoculum_unit": _unit_text(well.get("inoculum_unit"), normalize_units),
        "temperature": _numeric(metadata.get("temperature")),
        "temperature_unit": _unit_text(metadata.get("temperature_unit"), normalize_units),
        "culture_volume_uL": _numeric(
            _first(
                well.get("culture_volume_ul"),
                custom.get("Culture_volume_uL"),
                custom.get("Culture Volume uL"),
                _editable_value(plate_custom, "Culture_volume_uL", "Culture Volume uL"),
                _editable_value(experiment_custom, "Culture_volume_uL", "Culture Volume uL"),
            )
        ),
        "treatments": _treatments(well, custom, normalize_units=normalize_units),
        "extra": {name: _json_normal(custom.get(name)) for name in additional},
    }
    if not strain or not medium:
        well_id = _text(well.get("well_id"))
        if well_id:
            identity["unknown_well"] = well_id
        else:
            plate_id = _text(well.get("plate_id"))
            position = _text(well.get("position"))
            if not plate_id or not position:
                raise _error("Missing-condition well requires well_id or plate_id and position")
            identity["unknown_well"] = [plate_id, position.upper()]
    return json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def condition_json_object(value: object) -> dict[str, object]:
    """Parse a JSON object used by condition projections without application imports."""

    return _json_object(value)


def primary_condition_value(
    well: Mapping[str, object], custom: Mapping[str, object], field: str, legacy_field: str
) -> object:
    """Read a primary condition, retaining legacy JSON until an explicit layout edit.

    Legacy imports may have null structured columns despite a saved custom dose.
    Once a primary field has been edited, ``primary_condition_overrides`` records
    that its structured value is authoritative even when the edit cleared it.
    """

    structured = well.get(field)
    if structured is not None:
        return structured
    condition_custom = _json_object(well.get("condition_custom_json"))
    overrides = condition_custom.get("primary_condition_overrides", [])
    if not isinstance(overrides, list) or any(not isinstance(name, str) for name in overrides):
        raise _error("Primary condition overrides must be a list of field names")
    if field in overrides:
        return None
    return custom.get(legacy_field)


def _treatments(
    well: Mapping[str, object], custom: Mapping[str, object], *, normalize_units: bool
) -> list[list[str]]:
    triples: list[list[str]] = []
    for index in range(1, 4):
        treatment = custom.get(f"treatment_{index}")
        concentration = custom.get(f"conc_{index}")
        unit = custom.get(f"unit_{index}")
        if index == 1:
            treatment = primary_condition_value(well, custom, "treatment", "treatment_1")
            concentration = primary_condition_value(well, custom, "concentration", "conc_1")
            unit = primary_condition_value(well, custom, "concentration_unit", "unit_1")
        triple = [_text(treatment), _numeric(concentration), _unit_text(unit, normalize_units)]
        if any(triple):
            triples.append(triple)
    return sorted(triples)


def _extra_field_names(fields: tuple[str, ...]) -> tuple[str, ...]:
    names: set[str] = set()
    for raw_name in fields:
        if not isinstance(raw_name, str):
            raise _error("Condition additional field names must be text")
        name = raw_name.strip()
        if not name:
            continue
        if name.casefold() in _RESERVED_EXTRA_FIELDS:
            raise _error(f"Condition additional field {name} is reserved for identity bookkeeping")
        names.add(name)
    return tuple(sorted(names))


def _editable_value(source: Mapping[str, object], *names: str) -> object:
    legacy = _json_object(source.get("legacy_plate_meta"))
    for container in (legacy, source):
        for key in _EDITABLE_KEYS:
            if key in container:
                editable = _json_object(container[key])
                for name in names:
                    if name in editable:
                        return editable[name]
        for name in names:
            if name in container:
                return container[name]
    return None


def _first(*values: object) -> object:
    for value in values:
        if _text(value):
            return value
    return None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _unit_text(value: object, normalize_units: bool) -> str:
    return normalize_growth_unit(value) if normalize_units else _text(value)


def _numeric(value: object) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        raise _error("Condition numeric values must be finite")
    if isinstance(value, Decimal) and not value.is_finite():
        raise _error("Condition numeric values must be finite")
    raw = _text(value)
    if not raw:
        return ""
    if raw.casefold() in {
        "nan",
        "snan",
        "inf",
        "+inf",
        "-inf",
        "infinity",
        "+infinity",
        "-infinity",
    }:
        raise _error("Condition numeric values must be finite")
    if isinstance(value, bool) or _NUMBER.fullmatch(raw) is None:
        return raw
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if not number.is_finite():
        raise _error("Condition numeric values must be finite")
    if number == 0:
        return "0"
    parts = number.as_tuple()
    digits = list(parts.digits)
    exponent = parts.exponent
    assert isinstance(exponent, int)  # finite Decimal values have an integer exponent
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    sign = "-" if parts.sign else ""
    return f"{sign}{''.join(str(digit) for digit in digits)}e{exponent}"


def _json_normal(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, str | int | float | bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise _error("Condition values must be finite")
        return _numeric(value) if not isinstance(value, bool) else value
    if isinstance(value, list):
        return [_json_normal(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key).strip(): _json_normal(item) for key, item in sorted(value.items())}
    raise _error("Condition custom value must be JSON-compatible")


def _json_object(value: object) -> dict[str, object]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise _error("Condition metadata must contain a valid JSON object") from error
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _error("Condition metadata must contain a JSON object")
    return dict(value)


def _error(message: str) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message))
