"""Validated immutable growth-domain records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import WellPosition

GROWTH_NORMALIZATION_VERSION = "growth-normalize/1.0.0"
GROWTH_BACKGROUND_VERSION = "growth-background/1.0.0"


class BackgroundQcStatus(StrEnum):
    GOOD = "good"
    CAUTION = "caution"
    HIGH_CV = "high_cv"


@dataclass(frozen=True, slots=True)
class NormalizationSettings:
    t0_offset_minutes: float = 0.0
    interval_minutes: float = 10.0
    channel: str = "od600"

    def __post_init__(self) -> None:
        if not math.isfinite(self.t0_offset_minutes) or self.t0_offset_minutes < 0:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_TIME,
                    "T0 offset must be finite and nonnegative.",
                    value=self.t0_offset_minutes,
                )
            )
        if not math.isfinite(self.interval_minutes) or self.interval_minutes <= 0:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_INTERVAL,
                    "Time interval must be finite and greater than zero.",
                    value=self.interval_minutes,
                )
            )
        if not self.channel.strip():
            raise DomainValidationError(
                DomainIssue.error(IssueCode.INVALID_VALUE, "Channel cannot be empty.")
            )


@dataclass(frozen=True, slots=True)
class GrowthMeasurement:
    position: WellPosition
    time_index: int
    elapsed_microseconds: int
    channel: str
    value_raw: float

    def __post_init__(self) -> None:
        if self.time_index < 0 or self.elapsed_microseconds < 0:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_TIME,
                    "Measurement time identity must be nonnegative.",
                    time_index=self.time_index,
                    elapsed_microseconds=self.elapsed_microseconds,
                )
            )
        if not math.isfinite(self.value_raw):
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "Raw measurement must be finite.",
                    well=self.position.label,
                )
            )
        if not self.channel.strip():
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "Measurement channel cannot be empty.",
                    well=self.position.label,
                )
            )

    @property
    def elapsed_minutes(self) -> float:
        return self.elapsed_microseconds / 60_000_000


@dataclass(frozen=True, slots=True)
class GrowthNormalizationResult:
    measurements: tuple[GrowthMeasurement, ...]
    positions: tuple[WellPosition, ...]
    timepoints_microseconds: tuple[int, ...]
    issues: tuple[DomainIssue, ...]
    algorithm_version: str = GROWTH_NORMALIZATION_VERSION


@dataclass(frozen=True, slots=True)
class WellLabel:
    position: WellPosition
    label: str


@dataclass(frozen=True, slots=True)
class WellBackgroundAssignment:
    position: WellPosition
    is_blank: bool
    background_group: str = "plate"

    def __post_init__(self) -> None:
        if not self.background_group.strip():
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_VALUE,
                    "Background group cannot be empty.",
                    well=self.position.label,
                )
            )


@dataclass(frozen=True, slots=True)
class GrowthBackground:
    background_group: str
    channel: str
    time_index: int
    elapsed_microseconds: int
    mean_value: float
    std_value: float
    coefficient_of_variation: float
    blank_count: int
    qc_status: BackgroundQcStatus


@dataclass(frozen=True, slots=True)
class GrowthBackgroundResult:
    backgrounds: tuple[GrowthBackground, ...]
    issues: tuple[DomainIssue, ...]
    algorithm_version: str = GROWTH_BACKGROUND_VERSION


@dataclass(frozen=True, slots=True)
class CorrectedGrowthMeasurement:
    measurement: GrowthMeasurement
    background_group: str
    background_mean: float | None
    corrected_value: float | None


@dataclass(frozen=True, slots=True)
class GrowthCorrectionResult:
    measurements: tuple[CorrectedGrowthMeasurement, ...]
    issues: tuple[DomainIssue, ...]
    algorithm_version: str = GROWTH_BACKGROUND_VERSION
