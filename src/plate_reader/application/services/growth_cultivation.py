"""Preview and atomically persist growth cultivation identifiers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Protocol

from plate_reader.application.contracts import Actor, AssayType, PlateId, Role
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_cultivation_replicates import (
    plan_condition_replicates,
)
from plate_reader.application.services.growth_workflow import GrowthWorkflowRepository
from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.growth.cultivation import (
    DEFAULT_CULTIVATION_PATTERN,
    format_cultivation_id,
    generate_cultivation_id,
    normalize_cultivation_experiment_code,
)

_CONDITION_WELL_KEYS = (
    "LocalReplicate",
    "CultivationReplicate",
    "CultivationConditionKey",
    "CultivationReplicateScope",
    "CultivationConditionFields",
    "CultivationReplicateMode",
)


class GrowthCultivationRepository(GrowthWorkflowRepository, Protocol):
    """Growth repository with a metadata-only projection of reserved run codes."""

    def growth_cultivation_codes(self) -> tuple[dict[str, object], ...]: ...

    def growth_cultivation_wells(self) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True, slots=True)
class CultivationAssignment:
    position: str
    cultivation_run: str
    pattern: str | None = None
    experiment_code: str | None = None
    cultivation_replicate: int | None = None
    condition_key: str | None = None
    replicate_scope: str = ""
    condition_fields: tuple[str, ...] = ()
    matching_wells: int = 0
    matching_plates: int = 0


def prepare_condition_replicate_assignments(
    repository: GrowthCultivationRepository,
    snapshot: PlateSnapshot,
    registry: Mapping[str, object],
    assignments: Sequence[CultivationAssignment],
) -> tuple[CultivationAssignment, ...]:
    """Attach planned cross-plate replicate identities to selected wells for preview."""

    if not assignments:
        return ()
    registry_json = json_object(registry)
    plans = plan_condition_replicates(
        repository.growth_cultivation_wells(),
        target_plate_id=str(snapshot.plate_id),
        registry=registry_json,
    )
    prepared: list[CultivationAssignment] = []
    for assignment in assignments:
        position = _canonical_position(assignment.position)
        planned = plans.get(position)
        if planned is None:
            raise _domain_error(
                "Condition replicate assignment references a blank or unknown well.",
                position=position,
            )
        prepared.append(
            replace(
                assignment,
                position=position,
                cultivation_replicate=planned.replicate,
                condition_key=planned.condition_key,
                replicate_scope=planned.scope,
                condition_fields=planned.extra_fields,
                matching_wells=planned.matching_wells,
                matching_plates=planned.matching_plates,
            )
        )
    return tuple(prepared)


def suggested_cultivation_experiment_code(
    repository: GrowthCultivationRepository, plate_id: PlateId
) -> str:
    """Suggest a chronological number for an unsaved plate without reserving it."""

    suggestions = plan_cultivation_experiment_codes(repository.growth_cultivation_codes())
    try:
        return suggestions[str(plate_id)]
    except KeyError as error:
        raise _domain_error(
            "Growth plate has no cultivation code suggestion.", plate_id=str(plate_id)
        ) from error


def plan_cultivation_experiment_codes(
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, str]:
    """Plan all chronological numbers in one pass, preserving saved reservations."""

    plates: dict[str, Mapping[str, object]] = {}
    shared_values: dict[str, str] = {}
    shared_codes: dict[str, str] = {}
    well_codes: dict[str, set[str]] = {}
    reserved: set[str] = set()
    for row in rows:
        row_plate_id = str(row.get("plate_id", ""))
        record_type = row.get("record_type")
        if record_type == "plate":
            plates[row_plate_id] = row
        raw_code = _code_from_row(row)
        if record_type == "plate" and isinstance(raw_code, str) and raw_code:
            shared_values[row_plate_id] = raw_code
        code = _numeric_experiment_code(raw_code)
        if code is None:
            continue
        reserved.add(code)
        if record_type == "plate":
            shared_codes[row_plate_id] = code
        elif record_type == "well":
            well_codes.setdefault(row_plate_id, set()).add(code)

    suggestions = dict(shared_values)
    suggestions.update({key: value.zfill(3) for key, value in shared_codes.items()})
    for key, codes in well_codes.items():
        if key not in suggestions and len(codes) == 1:
            suggestions[key] = next(iter(codes)).zfill(3)

    unnumbered = sorted(
        (
            row
            for row_plate_id, row in plates.items()
            if row_plate_id not in shared_values and len(well_codes.get(row_plate_id, set())) != 1
        ),
        key=_plate_code_order,
    )
    number = "1"
    for plate_row in unnumbered:
        while number in reserved:
            number = _increment_experiment_code(number)
        suggestions[str(plate_row["plate_id"])] = number.zfill(3)
        number = _increment_experiment_code(number)
    return suggestions


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
    _normalize_registry_experiment_code(registry_json)
    if not assignments:
        return ()
    team_code = _registry_component(registry_json, "Team_Code")
    system_value = registry_json.get("CultivationSystemCode", "")
    system_code = system_value if isinstance(system_value, str) else ""
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
        condition_replicate = _condition_replicate(assignment, position)
        actual_replicate = (
            condition_replicate
            if condition_replicate is not None
            else _saved_replicate(replicate, position)
        )
        if assignment.pattern is None:
            system_code = _registry_component(registry_json, "CultivationSystemCode")
            cultivation = generate_cultivation_id(
                team_code, strain, system_code, assignment.cultivation_run, actual_replicate
            )
            run = f"{int(assignment.cultivation_run):03d}"
        else:
            experiment_code = normalize_cultivation_experiment_code(
                assignment.pattern, assignment.experiment_code or ""
            )
            cultivation = format_cultivation_id(
                assignment.pattern,
                team_code=team_code,
                strain=strain,
                system_code=system_code,
                cultivation_run=assignment.cultivation_run,
                replicate=actual_replicate,
                experiment_code=experiment_code,
                position=position,
            )
            run = _optional_run(assignment.cultivation_run)
        record: dict[str, object] = {
            **registry_json,
            "Well": position,
            "Cultivation": cultivation,
            "Team_Code": team_code,
            "Strain": strain,
            "CultivationSystemCode": system_code,
            "CultivationRun": run,
            "Replicate": actual_replicate,
        }
        if condition_replicate is not None:
            record.update(
                {
                    "LocalReplicate": replicate,
                    "CultivationReplicate": condition_replicate,
                    "CultivationConditionKey": assignment.condition_key,
                    "CultivationReplicateScope": assignment.replicate_scope,
                    "CultivationConditionFields": list(assignment.condition_fields),
                    "CultivationReplicateMode": "condition",
                    "MatchingWells": assignment.matching_wells,
                    "MatchingPlates": assignment.matching_plates,
                }
            )
        else:
            for key in _CONDITION_WELL_KEYS:
                record.pop(key, None)
            record.pop("MatchingWells", None)
            record.pop("MatchingPlates", None)
        if assignment.pattern is None:
            record.pop("CultivationIDPattern", None)
            record.pop("CultivationExperimentCode", None)
        else:
            record["CultivationIDPattern"] = assignment.pattern
            record["CultivationExperimentCode"] = experiment_code
        previews.append(record)
    _reject_duplicate_ids(previews)
    return tuple(previews)


class SaveGrowthCultivationsService:
    def __init__(self, repository: GrowthCultivationRepository) -> None:
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
        with self.repository.transaction():
            self._save_in_transaction(
                actor_id,
                plate_id,
                expected_updated_at,
                registry,
                assignments,
            )
        return _growth_snapshot(self.repository, plate_id)

    def _save_in_transaction(
        self,
        actor_id: str,
        plate_id: PlateId,
        expected_updated_at: str,
        registry: Mapping[str, object],
        assignments: Sequence[CultivationAssignment],
    ) -> None:
        snapshot = _growth_snapshot(self.repository, plate_id)
        registry_json = json_object(registry)
        _validate_registry(registry_json)
        _normalize_registry_experiment_code(registry_json)
        _validate_condition_assignments(self.repository, snapshot, registry_json, assignments)
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
                if "CultivationIDPattern" in record:
                    custom["CultivationIDPattern"] = record["CultivationIDPattern"]
                    custom["CultivationExperimentCode"] = record["CultivationExperimentCode"]
                else:
                    custom.pop("CultivationIDPattern", None)
                    custom.pop("CultivationExperimentCode", None)
                if record.get("CultivationReplicateMode") == "condition":
                    for key in _CONDITION_WELL_KEYS:
                        custom[key] = record[key]
                else:
                    for key in _CONDITION_WELL_KEYS:
                        custom.pop(key, None)
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
        _validate_experiment_code_reservations(
            self.repository,
            plate_id,
            registry_json,
            previews,
        )
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


def _growth_snapshot(repository: GrowthWorkflowRepository, plate_id: PlateId) -> PlateSnapshot:
    snapshot = repository.load_plate(plate_id)
    if snapshot is None:
        raise LookupError(f"Growth plate not found: {plate_id}")
    if str(snapshot.metadata["assay_type"]) != AssayType.GROWTH:
        raise ValueError(f"Plate is not a growth run: {plate_id}")
    return snapshot


def _code_from_row(row: Mapping[str, object]) -> object:
    return json_object(row.get("custom_json", {})).get("CultivationExperimentCode")


def _plate_code_order(row: Mapping[str, object]) -> tuple[int, str, int, str, str]:
    raw_date = row.get("experiment_date")
    valid_date: date | None = None
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        valid_date = raw_date
    elif isinstance(raw_date, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
        with suppress(ValueError):
            valid_date = date.fromisoformat(raw_date)
    created_at = row.get("created_at")
    valid_created = isinstance(created_at, str) and bool(created_at)
    return (
        0 if valid_date is not None else 1,
        valid_date.isoformat() if valid_date is not None else "",
        0 if valid_created else 1,
        created_at if isinstance(created_at, str) else "",
        str(row["plate_id"]),
    )


def _numeric_experiment_code(value: object) -> str | None:
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        return None
    digits = value.lstrip("0")
    return digits or None


def _normalize_registry_experiment_code(registry: dict[str, object]) -> None:
    if registry.get("CultivationIDPattern") != DEFAULT_CULTIVATION_PATTERN:
        return
    value = registry.get("CultivationExperimentCode")
    if not isinstance(value, str):
        raise _domain_error(
            "CultivationExperimentCode must be a positive decimal integer.",
            field="CultivationExperimentCode",
        )
    registry["CultivationExperimentCode"] = normalize_cultivation_experiment_code(
        DEFAULT_CULTIVATION_PATTERN,
        value,
    )


def _increment_experiment_code(value: str) -> str:
    digits = list(value)
    for index in range(len(digits) - 1, -1, -1):
        if digits[index] != "9":
            digits[index] = str(int(digits[index]) + 1)
            return "".join(digits)
        digits[index] = "0"
    return "1" + "".join(digits)


def _validate_experiment_code_reservations(
    repository: GrowthCultivationRepository,
    plate_id: PlateId,
    registry: Mapping[str, object],
    previews: Sequence[Mapping[str, object]],
) -> None:
    candidate_values = [registry.get("CultivationExperimentCode")]
    candidate_values.extend(
        record.get("CultivationExperimentCode")
        for record in previews
        if "CultivationIDPattern" in record
    )
    candidate_numbers = {
        number
        for value in candidate_values
        if (number := _numeric_experiment_code(value)) is not None
    }
    if not candidate_numbers:
        return
    for row in repository.growth_cultivation_codes():
        if str(row.get("plate_id")) == str(plate_id):
            continue
        reserved_number = _numeric_experiment_code(_code_from_row(row))
        if reserved_number in candidate_numbers:
            raise _domain_error(
                "Cultivation experiment number is already used by another plate; "
                "refresh the suggestion or use the next number.",
                experiment_code=reserved_number.zfill(3),
                conflicting_plate_id=str(row.get("plate_id")),
            )


def _registry_component(registry: Mapping[str, object], field: str) -> str:
    value = registry.get(field)
    if not isinstance(value, str) or not value:
        raise _domain_error("Cultivation registry is missing a required string.", field=field)
    return value


def _optional_run(value: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise _domain_error(
            "CultivationRun must be a positive decimal integer.", field="CultivationRun"
        )
    digits = value.lstrip("0")
    if not digits:
        raise _domain_error(
            "CultivationRun must be a positive decimal integer.", field="CultivationRun"
        )
    return digits.zfill(3)


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


def _condition_replicate(assignment: CultivationAssignment, position: str) -> int | None:
    if not _condition_fields_supplied(assignment):
        return None
    value = assignment.cultivation_replicate
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _domain_error("Invalid cultivation condition replicate.", position=position)
    if not isinstance(assignment.condition_key, str) or not assignment.condition_key:
        raise _domain_error("Missing cultivation condition key.", position=position)
    if (
        not isinstance(assignment.replicate_scope, str)
        or not isinstance(assignment.condition_fields, tuple)
        or any(not isinstance(field, str) or not field for field in assignment.condition_fields)
    ):
        raise _domain_error("Invalid cultivation condition settings.", position=position)
    return value


def _condition_fields_supplied(assignment: CultivationAssignment) -> bool:
    return (
        assignment.cultivation_replicate is not None
        or assignment.condition_key is not None
        or assignment.replicate_scope != ""
        or assignment.condition_fields != ()
        or assignment.matching_wells != 0
        or assignment.matching_plates != 0
    )


def _validate_condition_assignments(
    repository: GrowthCultivationRepository,
    snapshot: PlateSnapshot,
    registry: Mapping[str, object],
    assignments: Sequence[CultivationAssignment],
) -> None:
    if registry.get("CultivationReplicateMode") != "condition":
        if any(_condition_fields_supplied(assignment) for assignment in assignments):
            raise _domain_error(
                "Condition replicate overrides require condition mode; refresh preview."
            )
        return
    if not assignments:
        return
    planned = prepare_condition_replicate_assignments(repository, snapshot, registry, assignments)
    for original, current in zip(assignments, planned, strict=True):
        if (
            original.cultivation_replicate != current.cultivation_replicate
            or original.condition_key != current.condition_key
            or original.replicate_scope != current.replicate_scope
            or original.condition_fields != current.condition_fields
        ):
            raise _domain_error(
                "Condition replicate preview is stale; refresh preview before saving.",
                position=_canonical_position(original.position),
            )


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
