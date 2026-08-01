"""Shared MIC service mapping, hashing, and revision persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from plate_reader.application.contracts import AssayType, PlateId, RevisionId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.domain.common.plate import WellPosition
from plate_reader.domain.mic import MIC_ENDPOINT_VERSION, MicAnalysisResult, MicWell


class MicAnalysisRepository(Protocol):
    def add_analysis_revision(self, values: dict[str, object]) -> RevisionId: ...

    def insert_mic_well_calls(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def insert_mic_results(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...


def mic_input_sha256(wells: Sequence[MicWell], threshold: float) -> str:
    payload = {
        "threshold": threshold,
        "wells": sorted(
            (
                well.position.label,
                well.value_raw,
                well.is_blank,
                well.strain,
                well.treatment,
                well.concentration,
                well.concentration_unit,
                well.medium,
                well.replicate,
                well.notes,
                well.custom_labels,
            )
            for well in wells
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def persist_mic_analysis(
    repository: MicAnalysisRepository,
    plate_id: PlateId,
    actor_id: str,
    wells: Sequence[MicWell],
    well_ids: Mapping[WellPosition, str],
    analysis: MicAnalysisResult,
    id_factory: Callable[[], str],
    *,
    parameters: Mapping[str, object] | None = None,
) -> RevisionId:
    revision_id = RevisionId(id_factory())
    repository.add_analysis_revision(
        {
            "revision_id": revision_id,
            "plate_id": plate_id,
            "assay_type": AssayType.MIC,
            "algorithm_name": "mic_endpoint",
            "algorithm_version": MIC_ENDPOINT_VERSION,
            "parameters_json": {"threshold": analysis.threshold, **dict(parameters or {})},
            "input_sha256": mic_input_sha256(wells, analysis.threshold),
            "created_by": actor_id,
        }
    )
    repository.insert_mic_well_calls(
        revision_id,
        [
            {
                "well_id": well_ids[call.position],
                "background_value": call.background_value,
                "value_background_subtracted": call.value_background_subtracted,
                "growth_call": call.growth_call,
            }
            for call in analysis.well_calls
        ],
    )
    repository.insert_mic_results(
        revision_id,
        [
            {
                "result_id": id_factory(),
                "group_key": result.group_key,
                "strain": result.strain,
                "treatment": result.treatment,
                "medium": result.medium,
                "replicate": result.replicate,
                "mic_value": result.mic_value,
                "mic_operator": result.mic_operator,
                "mic_unit": result.mic_unit,
                "threshold_used": result.threshold_used,
                "lowest_tested_concentration": result.lowest_tested_concentration,
                "highest_tested_concentration": result.highest_tested_concentration,
                "concentrations_json": result.concentrations,
                "point_count": result.point_count,
                "calculation_status": result.calculation_status,
                "warning": "; ".join(issue.message for issue in result.issues) or None,
            }
            for result in analysis.results
        ],
    )
    return revision_id


def mic_wells_from_snapshot(snapshot: PlateSnapshot) -> tuple[MicWell, ...]:
    raw_by_well = {str(row["well_id"]): row["value_raw"] for row in snapshot.raw_observations}
    return tuple(
        MicWell(
            position=WellPosition.parse(str(well["position"])),
            value_raw=_number(raw_by_well[str(well["well_id"])]),
            is_blank=bool(well["is_blank"]),
            strain=_nullable_text(well.get("strain")),
            treatment=_nullable_text(well.get("treatment")),
            concentration=_nullable_number(well.get("concentration")),
            concentration_unit=_nullable_text(well.get("concentration_unit")) or "ug/mL",
            medium=_nullable_text(well.get("medium")),
            replicate=_positive_integer(well.get("replicate")),
            notes=_nullable_text(well.get("notes")),
            custom_labels=tuple(sorted(_json_string_map(well.get("custom_json")).items())),
        )
        for well in snapshot.wells
    )


def well_ids_from_snapshot(snapshot: PlateSnapshot) -> dict[WellPosition, str]:
    return {
        WellPosition.parse(str(well["position"])): str(well["well_id"]) for well in snapshot.wells
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expected a numeric MIC value")
    return float(value)


def _nullable_number(value: object) -> float | None:
    return None if value is None else _number(value)


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _nullable_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _json_string_map(value: object) -> dict[str, str]:
    if value in (None, "", {}):
        return {}
    parsed = json.loads(str(value)) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("MIC well custom_json must be an object")
    return {str(key): str(item) for key, item in parsed.items()}
