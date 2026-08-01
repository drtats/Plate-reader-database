"""Validated immutable MIC-domain records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import WellPosition

MIC_ENDPOINT_VERSION = "mic-endpoint/1.0.0"


class MicOperator(StrEnum):
    EQUAL = "="
    GREATER_THAN = ">"
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="


@dataclass(frozen=True, slots=True)
class MicWell:
    position: WellPosition
    value_raw: float
    is_blank: bool = False
    strain: str | None = None
    treatment: str | None = None
    concentration: float | None = None
    concentration_unit: str = "ug/mL"
    medium: str | None = None
    replicate: int = 1
    notes: str | None = None
    custom_labels: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.value_raw):
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "MIC endpoint value must be finite.",
                    well=self.position.label,
                )
            )
        if self.concentration is not None and (
            not math.isfinite(self.concentration) or self.concentration < 0
        ):
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_CONCENTRATION,
                    "MIC concentration must be finite and nonnegative.",
                    well=self.position.label,
                )
            )
        if self.replicate < 1:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "MIC replicate must be a positive integer.",
                    well=self.position.label,
                )
            )
        if not self.concentration_unit.strip():
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "MIC concentration unit cannot be empty.",
                    well=self.position.label,
                )
            )
        keys = [key for key, _value in self.custom_labels]
        if any(not key.strip() for key in keys) or len(keys) != len(set(keys)):
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "MIC custom-label names must be nonempty and unique.",
                    well=self.position.label,
                )
            )


@dataclass(frozen=True, slots=True)
class MicWellCall:
    position: WellPosition
    background_value: float
    value_background_subtracted: float
    growth_call: bool


@dataclass(frozen=True, slots=True)
class MicResult:
    group_key: str
    strain: str
    treatment: str
    medium: str
    replicate: int
    mic_value: float
    mic_operator: MicOperator
    mic_unit: str
    threshold_used: float
    lowest_tested_concentration: float
    highest_tested_concentration: float
    concentrations: tuple[float, ...]
    point_count: int
    issues: tuple[DomainIssue, ...] = ()
    calculation_status: str = "success"
    algorithm_version: str = MIC_ENDPOINT_VERSION


@dataclass(frozen=True, slots=True)
class MicAnalysisResult:
    background_value: float
    threshold: float
    well_calls: tuple[MicWellCall, ...]
    results: tuple[MicResult, ...]
    issues: tuple[DomainIssue, ...]
    algorithm_version: str = MIC_ENDPOINT_VERSION
