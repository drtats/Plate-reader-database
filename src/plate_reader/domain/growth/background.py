"""Deterministic growth background statistics and subtraction."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import WellPosition
from plate_reader.domain.growth.models import (
    BackgroundQcStatus,
    CorrectedGrowthMeasurement,
    GrowthBackground,
    GrowthBackgroundResult,
    GrowthCorrectionResult,
    GrowthMeasurement,
    WellBackgroundAssignment,
)


def calculate_backgrounds(
    measurements: Sequence[GrowthMeasurement],
    assignments: Sequence[WellBackgroundAssignment],
) -> GrowthBackgroundResult:
    assignment_by_position = _assignment_map(assignments)
    values: dict[tuple[str, str, int, int], list[float]] = defaultdict(list)
    for measurement in measurements:
        assignment = assignment_by_position.get(measurement.position)
        if assignment is not None and assignment.is_blank:
            key = (
                assignment.background_group.strip(),
                measurement.channel,
                measurement.time_index,
                measurement.elapsed_microseconds,
            )
            values[key].append(measurement.value_raw)

    issues: list[DomainIssue] = []
    blank_groups = {
        assignment.background_group.strip() for assignment in assignments if assignment.is_blank
    }
    used_groups = {assignment.background_group.strip() for assignment in assignments}
    if not blank_groups:
        issues.append(
            DomainIssue.warning(
                IssueCode.MISSING_BLANKS,
                "No wells are assigned as blanks; no backgrounds were calculated.",
            )
        )
    for missing_group in sorted(used_groups - blank_groups):
        issues.append(
            DomainIssue.warning(
                IssueCode.MISSING_BACKGROUND,
                "A background group has no blank wells.",
                background_group=missing_group,
            )
        )

    backgrounds: list[GrowthBackground] = []
    insufficient_groups: set[tuple[str, str]] = set()
    high_cv_groups: set[tuple[str, str]] = set()
    for (group, channel, time_index, elapsed), samples in sorted(values.items()):
        mean = math.fsum(samples) / len(samples)
        if len(samples) == 1:
            std = 0.0
            insufficient_groups.add((group, channel))
        else:
            std = statistics.stdev(samples)
        cv = std / max(abs(mean), 1e-9)
        status = _qc_status(cv)
        if status is BackgroundQcStatus.HIGH_CV:
            high_cv_groups.add((group, channel))
        backgrounds.append(
            GrowthBackground(
                background_group=group,
                channel=channel,
                time_index=time_index,
                elapsed_microseconds=elapsed,
                mean_value=mean,
                std_value=std,
                coefficient_of_variation=cv,
                blank_count=len(samples),
                qc_status=status,
            )
        )
    issues.extend(
        DomainIssue.warning(
            IssueCode.INSUFFICIENT_BLANKS,
            "A background group has only one blank; sample SD was defined as zero.",
            background_group=group,
            channel=channel,
        )
        for group, channel in sorted(insufficient_groups)
    )
    issues.extend(
        DomainIssue.warning(
            IssueCode.HIGH_BACKGROUND_CV,
            "A background group has a coefficient of variation at or above 0.10.",
            background_group=group,
            channel=channel,
        )
        for group, channel in sorted(high_cv_groups)
    )
    return GrowthBackgroundResult(tuple(backgrounds), tuple(issues))


def subtract_background(
    measurements: Sequence[GrowthMeasurement],
    assignments: Sequence[WellBackgroundAssignment],
    backgrounds: Sequence[GrowthBackground],
    *,
    manual_offset: float = 0.0,
) -> GrowthCorrectionResult:
    if not math.isfinite(manual_offset):
        raise DomainValidationError(
            DomainIssue.error(IssueCode.INVALID_VALUE, "Manual background offset must be finite.")
        )
    assignment_by_position = _assignment_map(assignments)
    background_by_key = {
        (
            background.background_group,
            background.channel,
            background.time_index,
            background.elapsed_microseconds,
        ): background
        for background in backgrounds
    }
    corrected: list[CorrectedGrowthMeasurement] = []
    issues: list[DomainIssue] = []
    missing: set[tuple[str, str]] = set()
    for measurement in measurements:
        assignment = assignment_by_position.get(measurement.position)
        group = assignment.background_group.strip() if assignment is not None else "plate"
        background = background_by_key.get(
            (
                group,
                measurement.channel,
                measurement.time_index,
                measurement.elapsed_microseconds,
            )
        )
        if background is None:
            missing.add((group, measurement.channel))
            corrected.append(CorrectedGrowthMeasurement(measurement, group, None, None))
            continue
        corrected.append(
            CorrectedGrowthMeasurement(
                measurement,
                group,
                background.mean_value,
                measurement.value_raw - background.mean_value - manual_offset,
            )
        )
    issues.extend(
        DomainIssue.warning(
            IssueCode.MISSING_BACKGROUND,
            "Measurements were not corrected because their background is missing.",
            background_group=group,
            channel=channel,
        )
        for group, channel in sorted(missing)
    )
    return GrowthCorrectionResult(tuple(corrected), tuple(issues))


def _assignment_map(
    assignments: Sequence[WellBackgroundAssignment],
) -> dict[WellPosition, WellBackgroundAssignment]:
    result: dict[WellPosition, WellBackgroundAssignment] = {}
    for assignment in assignments:
        if assignment.position in result:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.DUPLICATE_WELL,
                    "A well has more than one background assignment.",
                    well=assignment.position.label,
                )
            )
        result[assignment.position] = assignment
    return result


def _qc_status(coefficient_of_variation: float) -> BackgroundQcStatus:
    if coefficient_of_variation < 0.05:
        return BackgroundQcStatus.GOOD
    if coefficient_of_variation < 0.10:
        return BackgroundQcStatus.CAUTION
    return BackgroundQcStatus.HIGH_CV
