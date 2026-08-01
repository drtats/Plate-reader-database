"""Application-level summaries for persisted Growth background QC rows."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from plate_reader.domain.growth.models import BackgroundQcStatus


@dataclass(frozen=True, slots=True)
class GrowthBackgroundQcGroup:
    background_group: str
    channel: str
    timepoint_count: int
    blank_count_min: int
    blank_count_max: int
    mean_cv: float
    max_cv: float
    good_count: int
    caution_count: int
    high_cv_count: int


@dataclass(frozen=True, slots=True)
class GrowthBackgroundQcReport:
    groups: tuple[GrowthBackgroundQcGroup, ...]
    total_timepoints: int


class SummarizeGrowthBackgroundQcService:
    """Aggregate current-revision QC without coupling the UI to row mechanics."""

    def execute(self, backgrounds: tuple[dict[str, object], ...]) -> GrowthBackgroundQcReport:
        grouped: dict[tuple[str, str], list[tuple[float, int, BackgroundQcStatus]]] = defaultdict(
            list
        )
        for row in backgrounds:
            group = _required_text(row.get("background_group"), "background_group")
            channel = _required_text(row.get("channel"), "channel")
            cv = _finite_float(row.get("coefficient_of_variation"))
            blank_count = _positive_int(row.get("blank_count"))
            status = BackgroundQcStatus(_required_text(row.get("qc_status"), "qc_status"))
            grouped[(group, channel)].append((cv, blank_count, status))

        summaries: list[GrowthBackgroundQcGroup] = []
        for (group, channel), points in sorted(grouped.items()):
            cvs = [point[0] for point in points]
            blank_counts = [point[1] for point in points]
            statuses = [point[2] for point in points]
            summaries.append(
                GrowthBackgroundQcGroup(
                    background_group=group,
                    channel=channel,
                    timepoint_count=len(points),
                    blank_count_min=min(blank_counts),
                    blank_count_max=max(blank_counts),
                    mean_cv=math.fsum(cvs) / len(cvs),
                    max_cv=max(cvs),
                    good_count=statuses.count(BackgroundQcStatus.GOOD),
                    caution_count=statuses.count(BackgroundQcStatus.CAUTION),
                    high_cv_count=statuses.count(BackgroundQcStatus.HIGH_CV),
                )
            )
        return GrowthBackgroundQcReport(tuple(summaries), len(backgrounds))


def _required_text(value: object, field: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"Background QC {field} cannot be empty")
    return text


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Background QC values must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Background QC values must be finite")
    return result


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Background QC blank counts must be positive integers")
    return value
