"""Deterministic cultivation identifiers for growth wells."""

from __future__ import annotations

import re

from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode

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


def _component(value: object, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not value:
        raise _validation_error(field, "must be a non-empty string")
    if pattern.fullmatch(value) is None:
        raise _validation_error(field, "contains invalid or ambiguous delimiter characters")
    return value


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
