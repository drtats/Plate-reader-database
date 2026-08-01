"""Endpoint background correction, growth calling, and MIC interpretation."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import WellPosition
from plate_reader.domain.mic.models import (
    MicAnalysisResult,
    MicOperator,
    MicResult,
    MicWell,
    MicWellCall,
)


def analyze_mic_endpoint(wells: Sequence[MicWell], threshold: float) -> MicAnalysisResult:
    if not wells:
        raise DomainValidationError(
            DomainIssue.error(IssueCode.EMPTY_INPUT, "MIC analysis requires at least one well.")
        )
    if not math.isfinite(threshold) or threshold < 0:
        raise DomainValidationError(
            DomainIssue.error(
                IssueCode.INVALID_THRESHOLD,
                "MIC threshold must be finite and nonnegative.",
                threshold=threshold,
            )
        )
    _ensure_unique_positions(wells)
    blank_values = [well.value_raw for well in wells if well.is_blank]
    issues: list[DomainIssue] = []
    if blank_values:
        background_value = math.fsum(blank_values) / len(blank_values)
    else:
        background_value = 0.0
        issues.append(
            DomainIssue.warning(
                IssueCode.MISSING_BLANKS,
                "No MIC blank wells were provided; zero background was used.",
            )
        )

    calls = tuple(
        MicWellCall(
            position=well.position,
            background_value=background_value,
            value_background_subtracted=max(0.0, well.value_raw - background_value),
            growth_call=max(0.0, well.value_raw - background_value) >= threshold,
        )
        for well in wells
    )
    call_by_position = {call.position: call for call in calls}

    groups: dict[tuple[str, str, str, int, str], list[MicWell]] = defaultdict(list)
    missing_label_groups: set[tuple[str, str]] = set()
    for well in wells:
        if well.is_blank:
            continue
        if well.concentration is None:
            issues.append(
                DomainIssue.warning(
                    IssueCode.INVALID_CONCENTRATION,
                    "A nonblank MIC well without concentration was excluded.",
                    well=well.position.label,
                )
            )
            continue
        strain = _normalized_label(well.strain)
        treatment = _normalized_label(well.treatment)
        medium = _normalized_label(well.medium)
        for field, original in (
            ("strain", well.strain),
            ("treatment", well.treatment),
            ("medium", well.medium),
        ):
            if original is None or not original.strip():
                missing_label_groups.add((field, well.position.label))
        groups[(strain, treatment, medium, well.replicate, well.concentration_unit.strip())].append(
            well
        )
    issues.extend(
        DomainIssue.warning(
            IssueCode.MISSING_GROUP_LABEL,
            "A missing MIC grouping label was normalized to Unknown.",
            field=field,
            well=position,
        )
        for field, position in sorted(missing_label_groups)
    )

    results = tuple(
        _calculate_group(key, group_wells, call_by_position, threshold)
        for key, group_wells in sorted(groups.items())
    )
    if not results:
        issues.append(
            DomainIssue.warning(
                IssueCode.EMPTY_MIC_GROUP,
                "No valid nonblank MIC groups could be calculated.",
            )
        )
    return MicAnalysisResult(
        background_value=background_value,
        threshold=threshold,
        well_calls=calls,
        results=results,
        issues=tuple(issues),
    )


def _calculate_group(
    key: tuple[str, str, str, int, str],
    wells: Sequence[MicWell],
    calls: dict[WellPosition, MicWellCall],
    threshold: float,
) -> MicResult:
    concentration_growth: dict[float, bool] = {}
    for well in wells:
        assert well.concentration is not None
        concentration_growth[well.concentration] = (
            concentration_growth.get(well.concentration, False) or calls[well.position].growth_call
        )
    concentrations = tuple(sorted(concentration_growth))
    first_no_growth = next(
        (index for index, value in enumerate(concentrations) if not concentration_growth[value]),
        None,
    )
    result_issues: list[DomainIssue] = []
    if first_no_growth is None:
        mic_value = concentrations[-1]
        operator = MicOperator.GREATER_THAN
    else:
        mic_value = concentrations[first_no_growth]
        operator = MicOperator.LESS_THAN_OR_EQUAL if first_no_growth == 0 else MicOperator.EQUAL
        bounce = next(
            (
                value
                for value in concentrations[first_no_growth + 1 :]
                if concentration_growth[value]
            ),
            None,
        )
        if bounce is not None:
            result_issues.append(
                DomainIssue.warning(
                    IssueCode.GROWTH_BOUNCE,
                    f"Growth bounce detected at {bounce} after no-growth at {mic_value}",
                    concentration=bounce,
                    first_no_growth=mic_value,
                )
            )
    strain, treatment, medium, replicate, unit = key
    group_key = json.dumps(key, ensure_ascii=True, separators=(",", ":"))
    return MicResult(
        group_key=group_key,
        strain=strain,
        treatment=treatment,
        medium=medium,
        replicate=replicate,
        mic_value=mic_value,
        mic_operator=operator,
        mic_unit=unit,
        threshold_used=threshold,
        lowest_tested_concentration=concentrations[0],
        highest_tested_concentration=concentrations[-1],
        concentrations=concentrations,
        point_count=len(wells),
        issues=tuple(result_issues),
    )


def _normalized_label(value: str | None) -> str:
    normalized = value.strip() if value is not None else ""
    return normalized or "Unknown"


def _ensure_unique_positions(wells: Sequence[MicWell]) -> None:
    seen: set[WellPosition] = set()
    for well in wells:
        if well.position in seen:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.DUPLICATE_WELL,
                    "MIC input contains the same physical well more than once.",
                    well=well.position.label,
                )
            )
        seen.add(well.position)
