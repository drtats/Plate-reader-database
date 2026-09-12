"""Preview and atomically persist growth cultivation identifiers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from plate_reader.application.contracts import Actor, AssayType, PlateId, Role
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_workflow import GrowthWorkflowRepository
from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.growth.cultivation import generate_cultivation_id


@dataclass(frozen=True, slots=True)
class CultivationAssignment:
    position: str
    cultivation_run: str


def json_object(value: object) -> dict[str, object]:
    """Return an independent JSON object without coercing non-JSON values."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Expected valid JSON object text") from error
    else:
        parsed = value
    if not isinstance(parsed, Mapping):
        raise ValueError("Expected a JSON object")
    normalized = _strict_json_value(parsed)
    if not isinstance(normalized, dict):  # pragma: no cover - guaranteed by the check above
        raise ValueError("Expected a JSON object")
    return normalized


def preview_cultivations(
    snapshot: PlateSnapshot,
    registry: Mapping[str, object],
    assignments: Sequence[CultivationAssignment],
) -> tuple[dict[str, object], ...]:
    """Generate cultivation records using only the snapshot's saved well state."""

    registry_json = json_object(registry)
    _validate_registry(registry_json)
    if not assignments:
        return ()
    team_code = _registry_component(registry_json, "Team_Code")
    system_code = _registry_component(registry_json, "CultivationSystemCode")
    wells = {_canonical_position(well.get("position")): well for well in snapshot.wells}
    seen_positions: set[str] = set()
    previews: list[dict[str, object]] = []
    for assignment in assignments:
        position = _canonical_position(assignment.position)
        if position in seen_positions:
            raise _domain_error("Duplicate cultivation assignment.", position=position)
        seen_positions.add(position)
        well = wells.get(position)
        if well is None:
            raise _domain_error(
                "Cultivation assignment references an unknown well.", position=position
            )
        strain = well.get("strain")
        if not isinstance(strain, str) or not strain:
            raise _domain_error("Assigned well is missing a saved strain.", position=position)
        replicate = well.get("replicate")
        cultivation = generate_cultivation_id(
            team_code,
            strain,
            system_code,
            assignment.cultivation_run,
            _saved_replicate(replicate, position),
        )
        run = int(assignment.cultivation_run)
        previews.append(
            {
                **registry_json,
                "Well": position,
                "Cultivation": cultivation,
                "Team_Code": team_code,
                "Strain": strain,
                "CultivationSystemCode": system_code,
                "CultivationRun": f"{run:03d}",
                "Replicate": replicate,
            }
        )
    _reject_duplicate_ids(previews)
    return tuple(previews)


class SaveGrowthCultivationsService:
    def __init__(self, repository: GrowthWorkflowRepository) -> None:
        self.repository = repository

    def execute(
        self,
        actor: Actor,
        plate_id: PlateId,
        expected_updated_at: str,
        registry: Mapping[str, object],
        assignments: Sequence[CultivationAssignment],
    ) -> PlateSnapshot:
        actor_id = require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
        snapshot = _growth_snapshot(self.repository, plate_id)
        registry_json = json_object(registry)
        _validate_registry(registry_json)
        previews = preview_cultivations(snapshot, registry_json, assignments)
        assigned_positions = {str(record["Well"]) for record in previews}
        final_ids: list[dict[str, object]] = list(previews)
        changes: list[dict[str, object]] = []
        audit_changes: list[dict[str, object]] = []
        previews_by_position = {str(record["Well"]): record for record in previews}
        for well in snapshot.wells:
            position = _canonical_position(well.get("position"))
            custom = json_object(well.get("custom_json", {}))
            if position in assigned_positions:
                record = previews_by_position[position]
                existing_id = _saved_cultivation_id(custom.get("Cultivation"), position)
                audit_changes.append(
                    {
                        "position": position,
                        "before": existing_id,
                        "after": record["Cultivation"],
                    }
                )
                custom.update(
                    {
                        "Cultivation": record["Cultivation"],
                        "Team_Code": record["Team_Code"],
                        "CultivationSystemCode": record["CultivationSystemCode"],
                        "CultivationRun": record["CultivationRun"],
                    }
                )
                changes.append({"position": position, "custom_json": custom})
                continue
            existing_id = _saved_cultivation_id(custom.get("Cultivation"), position)
            if existing_id is None:
                continue
            final_ids.append({"Well": position, "Cultivation": existing_id})
        _reject_duplicate_ids(final_ids)

        plate_custom = json_object(snapshot.metadata.get("plate_custom_json", {}))
        previous_registry = json_object(plate_custom.get("cultivation_registry", {}))
        plate_custom["cultivation_registry"] = registry_json
        with self.repository.transaction():
            self.repository.update_plate_metadata(
                plate_id,
                expected_updated_at,
                {"custom_json": plate_custom},
            )
            if changes:
                self.repository.update_well_layout(plate_id, changes)
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "cultivation_metadata_updated",
                    "entity_type": "plate",
                    "entity_id": plate_id,
                    "details_json": {
                        "positions": [str(record["Well"]) for record in previews],
                        "registry": {"before": previous_registry, "after": registry_json},
                        "cultivations": audit_changes,
                    },
                }
            )
        return _growth_snapshot(self.repository, plate_id)


def _growth_snapshot(repository: GrowthWorkflowRepository, plate_id: PlateId) -> PlateSnapshot:
    snapshot = repository.load_plate(plate_id)
    if snapshot is None:
        raise LookupError(f"Growth plate not found: {plate_id}")
    if str(snapshot.metadata["assay_type"]) != AssayType.GROWTH:
        raise ValueError(f"Plate is not a growth run: {plate_id}")
    return snapshot


def _registry_component(registry: Mapping[str, object], field: str) -> str:
    value = registry.get(field)
    if not isinstance(value, str) or not value:
        raise _domain_error("Cultivation registry is missing a required string.", field=field)
    return value


def _validate_registry(registry: Mapping[str, object]) -> None:
    value = registry.get("InoculationDateTime")
    if "InoculationDateTime" not in registry:
        return
    if value == "":
        return
    if not isinstance(value, str) or ("T" not in value and " " not in value):
        raise _domain_error(
            "InoculationDateTime must be an ISO date-time string.",
            field="InoculationDateTime",
        )
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise _domain_error(
            "InoculationDateTime must be an ISO date-time string.",
            field="InoculationDateTime",
        ) from error


def _canonical_position(value: object) -> str:
    if not isinstance(value, str):
        raise _domain_error("Well position must be a string.")
    try:
        return WellPosition.parse(value).label
    except DomainValidationError as error:
        raise _domain_error("Invalid cultivation well position.", position=value) from error


def _saved_replicate(value: object, position: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _domain_error("Assigned well has no valid saved replicate.", position=position)
    return value


def _saved_cultivation_id(value: object, position: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _domain_error("Saved cultivation ID must be a string.", position=position)
    return value


def _reject_duplicate_ids(records: Sequence[Mapping[str, object]]) -> None:
    positions_by_id: dict[str, str] = {}
    for record in records:
        cultivation = str(record["Cultivation"])
        position = str(record["Well"])
        prior = positions_by_id.get(cultivation)
        if prior is not None:
            raise _domain_error(
                "Duplicate cultivation ID.",
                cultivation=cultivation,
                first_position=prior,
                second_position=position,
            )
        positions_by_id[cultivation] = position


def _strict_json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, list):
        return [_strict_json_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            result[key] = _strict_json_value(item)
        return result
    raise ValueError(f"Value of type {type(value).__name__} is not valid JSON")


def _domain_error(message: str, **context: object) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message, **context))
