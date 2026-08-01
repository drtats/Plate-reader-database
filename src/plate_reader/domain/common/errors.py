"""Stable, UI-independent domain issue codes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IssueSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class IssueCode(StrEnum):
    EMPTY_INPUT = "empty_input"
    INVALID_PLATE_FORMAT = "invalid_plate_format"
    INVALID_WELL = "invalid_well"
    DUPLICATE_WELL = "duplicate_well"
    MISSING_WELLS = "missing_wells"
    UNKNOWN_COLUMN = "unknown_column"
    INVALID_LAYOUT = "invalid_layout"
    INVALID_TIME = "invalid_time"
    NEGATIVE_TIME = "negative_time"
    DUPLICATE_TIME = "duplicate_time"
    INVALID_INTERVAL = "invalid_interval"
    INVALID_VALUE = "invalid_value"
    MISSING_BLANKS = "missing_blanks"
    INSUFFICIENT_BLANKS = "insufficient_blanks"
    MISSING_BACKGROUND = "missing_background"
    HIGH_BACKGROUND_CV = "high_background_cv"
    INVALID_THRESHOLD = "invalid_threshold"
    INVALID_CONCENTRATION = "invalid_concentration"
    MISSING_GROUP_LABEL = "missing_group_label"
    EMPTY_MIC_GROUP = "empty_mic_group"
    GROWTH_BOUNCE = "growth_bounce"


@dataclass(frozen=True, slots=True)
class DomainIssue:
    code: IssueCode
    severity: IssueSeverity
    message: str
    context: tuple[tuple[str, str], ...] = ()

    @classmethod
    def warning(cls, code: IssueCode, message: str, **context: object) -> DomainIssue:
        return cls(code, IssueSeverity.WARNING, message, _context(context))

    @classmethod
    def error(cls, code: IssueCode, message: str, **context: object) -> DomainIssue:
        return cls(code, IssueSeverity.ERROR, message, _context(context))


class DomainValidationError(ValueError):
    """Validation failure carrying stable machine-readable issues."""

    def __init__(self, *issues: DomainIssue) -> None:
        if not issues:
            raise ValueError("DomainValidationError requires at least one issue")
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in issues))

    @property
    def primary_code(self) -> IssueCode:
        return self.issues[0].code


def _context(values: dict[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in values.items()))
