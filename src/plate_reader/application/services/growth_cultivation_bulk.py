"""Load and atomically patch shared cultivation metadata for Growth runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from plate_reader.application.contracts import Actor, PlateId, Role
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_cultivation import json_object, preview_cultivations
from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode

CULTIVATION_SHARED_FIELDS = (
    "CultivationReplicateScope",
    "CultivationConditionFields",
    "Team_Code",
    "CultivationSystemCode",
    "CultivationExperiment",
    "InoculationDateTime",
    "ProgramMetric",
    "EquipmentMakeModel",
    "Objective",
    "CultivationProtocol",
    "SampleAnalysisProtocol",
    "Comment",
)

_MAX_TARGETS = 500


@dataclass(frozen=True, slots=True)
class GrowthCultivationMetadata:
    plate_id: PlateId
    experiment_name: str
    plate_name: str
    updated_at: str
    registry: dict[str, object]


@dataclass(frozen=True, slots=True)
class GrowthCultivationTarget:
    plate_id: PlateId
    expected_updated_at: str


class StaleGrowthCultivationMetadataError(ValueError):
    """A selected Growth run changed after the bulk editor was opened."""

    def __init__(self, plate_ids: Sequence[PlateId]) -> None:
        self.plate_ids = tuple(plate_ids)
        super().__init__(
            "Growth cultivation metadata changed since it was loaded: " + ", ".join(self.plate_ids)
        )


class GrowthCultivationBulkRepository(Protocol):
    def growth_cultivation_metadata(
        self, plate_ids: Sequence[PlateId]
    ) -> tuple[dict[str, object], ...]: ...

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def update_plate_metadata(
        self, plate_id: PlateId, expected_updated_at: str, changes: dict[str, object]
    ) -> str: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...


class LoadBulkGrowthCultivationMetadataService:
    def __init__(self, repository: GrowthCultivationBulkRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, plate_ids: Sequence[PlateId]
    ) -> tuple[GrowthCultivationMetadata, ...]:
        require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
        requested = _validate_plate_ids(plate_ids)
        return tuple(
            _metadata_from_row(row) for row in _load_complete_rows(self.repository, requested)
        )


class UpdateBulkGrowthCultivationMetadataService:
    def __init__(self, repository: GrowthCultivationBulkRepository) -> None:
        self.repository = repository

    def execute(
        self,
        actor: Actor,
        targets: Sequence[GrowthCultivationTarget],
        changes: Mapping[str, str],
        *,
        fill_missing_only: bool = True,
    ) -> tuple[PlateId, ...]:
        actor_id = require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
        validated_targets = _validate_targets(targets)
        patch = _validate_changes(changes)
        if not isinstance(fill_missing_only, bool):
            raise _domain_error("Fill-missing mode must be a boolean.")
        plate_ids = tuple(target.plate_id for target in validated_targets)

        changed_ids: list[PlateId] = []
        with self.repository.transaction():
            rows = _load_complete_rows(self.repository, plate_ids)
            rows_by_id = {PlateId(str(row["plate_id"])): row for row in rows}
            current_by_id = {
                plate_id: _metadata_from_row(row) for plate_id, row in rows_by_id.items()
            }
            stale_ids = tuple(
                target.plate_id
                for target in validated_targets
                if current_by_id[target.plate_id].updated_at != target.expected_updated_at
            )
            if stale_ids:
                raise StaleGrowthCultivationMetadataError(stale_ids)

            for target in validated_targets:
                item = current_by_id[target.plate_id]
                after = _merge_registry(item.registry, patch, fill_missing_only=fill_missing_only)
                if after == item.registry:
                    continue
                plate_custom = json_object(rows_by_id[target.plate_id].get("plate_custom_json", {}))
                plate_custom["cultivation_registry"] = after
                self.repository.update_plate_metadata(
                    target.plate_id,
                    target.expected_updated_at,
                    {"custom_json": plate_custom},
                )
                self.repository.append_provenance(
                    {
                        "actor_id": actor_id,
                        "event_type": "cultivation_metadata_updated",
                        "entity_type": "plate",
                        "entity_id": target.plate_id,
                        "details_json": {
                            "registry": {"before": item.registry, "after": after},
                            "bulk": {
                                "fill_missing_only": fill_missing_only,
                                "fields": list(patch),
                                "target_count": len(validated_targets),
                            },
                        },
                    }
                )
                changed_ids.append(target.plate_id)
        return tuple(changed_ids)


def _load_complete_rows(
    repository: GrowthCultivationBulkRepository, plate_ids: tuple[PlateId, ...]
) -> tuple[dict[str, object], ...]:
    rows = repository.growth_cultivation_metadata(plate_ids)
    by_id = {PlateId(str(row.get("plate_id"))): row for row in rows}
    missing = tuple(plate_id for plate_id in plate_ids if plate_id not in by_id)
    if missing:
        raise LookupError(f"Growth plates not found: {', '.join(missing)}")
    return tuple(by_id[plate_id] for plate_id in plate_ids)


def _metadata_from_row(row: Mapping[str, object]) -> GrowthCultivationMetadata:
    plate_custom = json_object(row.get("plate_custom_json", {}))
    registry = json_object(plate_custom.get("cultivation_registry", {}))
    return GrowthCultivationMetadata(
        plate_id=PlateId(str(row["plate_id"])),
        experiment_name=str(row["experiment_name"]),
        plate_name=str(row["plate_name"]),
        updated_at=str(row["updated_at"]),
        registry=registry,
    )


def _validate_plate_ids(plate_ids: Sequence[PlateId]) -> tuple[PlateId, ...]:
    requested = tuple(plate_ids)
    if not requested:
        raise _domain_error("Select at least one Growth run.")
    if len(requested) > _MAX_TARGETS:
        raise _domain_error("No more than 500 Growth runs may be selected.", count=len(requested))
    if any(not isinstance(plate_id, str) or not plate_id.strip() for plate_id in requested):
        raise _domain_error("Growth run IDs must be non-empty strings.")
    if len(set(requested)) != len(requested):
        raise _domain_error("Growth run IDs must be unique.")
    return requested


def _validate_targets(
    targets: Sequence[GrowthCultivationTarget],
) -> tuple[GrowthCultivationTarget, ...]:
    validated = tuple(targets)
    _validate_plate_ids(tuple(target.plate_id for target in validated))
    if any(
        not isinstance(target.expected_updated_at, str) or not target.expected_updated_at.strip()
        for target in validated
    ):
        raise _domain_error("Each Growth run requires a non-empty version.")
    return validated


def _validate_changes(changes: Mapping[str, str]) -> dict[str, str]:
    if not changes:
        raise _domain_error("Select at least one cultivation metadata field.")
    patch: dict[str, str] = {}
    for field, value in changes.items():
        if not isinstance(field, str) or field not in CULTIVATION_SHARED_FIELDS:
            raise _domain_error("Unknown cultivation metadata field.", field=field)
        if not isinstance(value, str):
            raise _domain_error("Cultivation metadata values must be strings.", field=field)
        patch[field] = value
    _validate_registry_patch(patch)
    return patch


def _validate_registry_patch(patch: Mapping[str, str]) -> None:
    # Reuse the canonical optional date-time validation without requiring a plate
    # snapshot or any well/raw-data access.
    empty_snapshot = PlateSnapshot(PlateId("validation"), {}, (), (), ())
    preview_cultivations(empty_snapshot, patch, ())


def _merge_registry(
    registry: Mapping[str, object],
    patch: Mapping[str, str],
    *,
    fill_missing_only: bool,
) -> dict[str, object]:
    merged = json_object(registry)
    for field, value in patch.items():
        current = merged.get(field)
        if (
            fill_missing_only
            and current is not None
            and (not isinstance(current, str) or current.strip())
        ):
            continue
        merged[field] = value
    return merged


def _domain_error(message: str, **context: object) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message, **context))
