"""Deterministic cultivation identifiers for growth wells."""

from __future__ import annotations

import re
from string import Formatter

from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode, WellPosition

DEFAULT_CULTIVATION_PATTERN = "{team}-EXP-{strain}-{experiment}-{well}-R{replicate}"
LEGACY_CULTIVATION_PATTERN = "{team}-EXP-{strain}-{system}{run}R{replicate}"

_PATTERN_FIELDS = frozenset({"team", "strain", "system", "run", "experiment", "well", "replicate"})
_SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,256}")

_TEAM_CODE = re.compile(r"[A-Za-z0-9]+")
_STRAIN = re.compile(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*")
_SYSTEM_CODE = re.compile(r"[A-Za-z](?:[A-Za-z0-9]*[A-Za-z])?")
_DECIMAL_RUN = re.compile(r"[0-9]+")


def generate_cultivation_id(
    team_code: str,
    strain: str,
    system_code: str,
    cultivation_run: str,
    replicate: int,
) -> str:
    """Build a stable cultivation ID from explicitly supplied components.

    ``EXP`` is a fixed namespace segment. The component alphabets reserve ``-``
    between the other segments. Underscores remain valid inside strain names,
    and digits remain valid inside a system code. A system code must end with a
    letter so its boundary with the numeric cultivation run remains unambiguous.
    """

    team = _component(team_code, "team_code", _TEAM_CODE)
    strain_name = _component(strain, "strain", _STRAIN)
    system = _component(system_code, "system_code", _SYSTEM_CODE)
    run = _run_number(cultivation_run)
    replicate_number = _replicate_number(replicate)
    return f"{team}-EXP-{strain_name}-{system}{run:03d}R{replicate_number}"


def format_cultivation_id(
    pattern: str,
    *,
    team_code: str,
    strain: str,
    system_code: str,
    cultivation_run: str,
    replicate: int,
    experiment_code: str,
    position: str,
) -> str:
    """Render only the documented placeholders, then validate the complete ID.

    Field access, conversions and format specifications are deliberately excluded;
    this is a bounded naming pattern, not a general Python format string.
    """

    if not isinstance(pattern, str) or not pattern:
        raise _validation_error("pattern", "must be a non-empty string")
    if ":" in pattern:
        raise _validation_error("pattern", "contains an unsupported format specification")
    try:
        parts = tuple(Formatter().parse(pattern))
    except ValueError as error:
        raise _validation_error("pattern", "has invalid braces") from error
    fields = {field for _, field, _, _ in parts if field is not None}
    if not fields:
        raise _validation_error("pattern", "must include at least one placeholder")
    for _, field, format_spec, conversion in parts:
        if field is not None and (
            field not in _PATTERN_FIELDS or format_spec or conversion is not None
        ):
            raise _validation_error("pattern", "contains an unsupported placeholder")

    experiment_code = normalize_cultivation_experiment_code(pattern, experiment_code)
    values: dict[str, str] = {}
    components: dict[str, object] = {
        "team": team_code,
        "strain": strain,
        "system": system_code,
        "experiment": experiment_code,
    }
    for field in fields & components.keys():
        value = components[field]
        if not isinstance(value, str) or not value:
            raise _validation_error(field, "must be a non-empty string")
        values[field] = value
    if "run" in fields:
        values["run"] = _pattern_run(cultivation_run)
    if "replicate" in fields:
        values["replicate"] = str(_replicate_number(replicate))
    if "well" in fields:
        if not isinstance(position, str):
            raise _validation_error("well", "must be a valid well position")
        try:
            well = WellPosition.parse(position)
        except DomainValidationError as error:
            raise _validation_error("well", "must be a valid well position") from error
        values["well"] = f"{well.label[0]}{well.column_index + 1:02d}"

    result = "".join(
        literal + (values[field] if field is not None else "") for literal, field, _, _ in parts
    )
    if _SAFE_ID.fullmatch(result) is None:
        raise _validation_error(
            "pattern", "produces an invalid ID (safe characters, 1-256 characters required)"
        )
    return result


def normalize_cultivation_experiment_code(pattern: str, experiment_code: str) -> str:
    """Canonicalize the recommended numeric run code; custom patterns keep their code."""

    if pattern != DEFAULT_CULTIVATION_PATTERN:
        return experiment_code
    if (
        not isinstance(experiment_code, str)
        or not experiment_code
        or not experiment_code.isascii()
        or not experiment_code.isdecimal()
    ):
        raise _validation_error("experiment_code", "must be a positive decimal integer")
    digits = experiment_code.lstrip("0")
    if not digits:
        raise _validation_error("experiment_code", "must be a positive decimal integer")
    return digits.zfill(3)


def _component(value: object, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not value:
        raise _validation_error(field, "must be a non-empty string")
    if pattern.fullmatch(value) is None:
        raise _validation_error(field, "contains invalid or ambiguous delimiter characters")
    return value


def _pattern_run(value: object) -> str:
    if not isinstance(value, str) or _DECIMAL_RUN.fullmatch(value) is None:
        raise _validation_error("cultivation_run", "must be a positive decimal integer")
    digits = value.lstrip("0")
    if not digits:
        raise _validation_error("cultivation_run", "must be a positive decimal integer")
    return digits.zfill(3)


def _run_number(value: object) -> int:
    if not isinstance(value, str) or _DECIMAL_RUN.fullmatch(value) is None:
        raise _validation_error("cultivation_run", "must be a positive decimal integer")
    number = int(value)
    if number < 1:
        raise _validation_error("cultivation_run", "must be a positive decimal integer")
    return number


def _replicate_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _validation_error("replicate", "must be a positive integer")
    return value


def _validation_error(field: str, reason: str) -> DomainValidationError:
    return DomainValidationError(
        DomainIssue.error(
            IssueCode.INVALID_VALUE,
            f"Cultivation {field} {reason}.",
            field=field,
        )
    )
