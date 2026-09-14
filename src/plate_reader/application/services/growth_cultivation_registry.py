"""Preview and persist plate/condition cultivation identities without reading raw data."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

from plate_reader.application.contracts import Actor, PlateId, Role
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_cultivation import (
    GrowthCultivationRepository,
    json_object,
)
from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.growth.cultivation_registry import (
    SCHEME,
    RegistryPlatePlan,
    RegistrySettings,
    plan_plate_condition_cultivations,
)

_MAX_PLATES = 500


class RegistryRepository(GrowthCultivationRepository, Protocol):
    """Repository projections needed by the metadata-only registry workflow."""

    def growth_cultivation_metadata(
        self, plate_ids: Sequence[PlateId]
    ) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True, slots=True)
class RegistryPreview:
    plate_ids: tuple[PlateId, ...]
    settings: RegistrySettings
    plates: tuple[RegistryPlatePlan, ...]
    expected_versions: tuple[tuple[str, str], ...]


class StaleGrowthCultivationRegistryError(ValueError):
    """The library or selected runs changed since the preview was prepared."""


class PreviewGrowthCultivationRegistryService:
    def __init__(self, repository: RegistryRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, plate_ids: tuple[PlateId, ...], settings: RegistrySettings
    ) -> RegistryPreview:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        requested = _validated_plate_ids(plate_ids)
        if not isinstance(settings, RegistrySettings):
            raise _error("Invalid cultivation registry settings.")
        metadata = self.repository.growth_cultivation_metadata(requested)
        rows = self.repository.growth_cultivation_wells()
        return _preview_from_projection(requested, settings, metadata, rows)


def _preview_from_projection(
    requested: tuple[PlateId, ...],
    settings: RegistrySettings,
    metadata: Sequence[dict[str, object]],
    rows: Sequence[dict[str, object]],
) -> RegistryPreview:
    """Plan from one read set; callers own authorization and transaction boundaries."""

    if not isinstance(settings, RegistrySettings):
        raise _error("Invalid cultivation registry settings.")
    by_id = {str(row["plate_id"]): row for row in metadata}
    missing = tuple(plate_id for plate_id in requested if plate_id not in by_id)
    if missing:
        raise LookupError(f"Active Growth plates not found: {', '.join(missing)}")
    # The complete library reserves numbers on deleted runs too.
    plans = plan_plate_condition_cultivations(rows, tuple(requested), settings=settings)
    if {plan.plate_id for plan in plans} != set(requested) or len(plans) != len(requested):
        raise _error("Cultivation planner did not return each selected plate exactly once.")
    plans_by_id = {plan.plate_id: plan for plan in plans}
    return RegistryPreview(
        requested,
        settings,
        tuple(plans_by_id[plate_id] for plate_id in requested),
        tuple((plate_id, str(by_id[plate_id]["updated_at"])) for plate_id in requested),
    )


class SaveGrowthCultivationRegistryService:
    def __init__(self, repository: RegistryRepository) -> None:
        self.repository = repository

    def execute(self, actor: Actor, preview: RegistryPreview) -> tuple[PlateId, ...]:
        if not isinstance(preview, RegistryPreview):
            raise _error("Save requires a cultivation registry preview.")
        actor_id = require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
        _validated_plate_ids(preview.plate_ids)
        changed_ids: list[PlateId] = []
        with self.repository.transaction():
            # Recheck the stored role and all global reservations under the write lock.
            require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
            metadata = self.repository.growth_cultivation_metadata(preview.plate_ids)
            library_rows = self.repository.growth_cultivation_wells()
            fresh = _preview_from_projection(
                preview.plate_ids, preview.settings, metadata, library_rows
            )
            if not _same_preview(preview, fresh):
                raise StaleGrowthCultivationRegistryError(
                    "Growth cultivation registry preview is stale; preview again before saving."
                )
            metadata_by_id = {str(row["plate_id"]): row for row in metadata}
            rows_by_plate: dict[str, list[dict[str, object]]] = {}
            for row in library_rows:
                rows_by_plate.setdefault(str(row["plate_id"]), []).append(row)
            _validate_planned_id_collisions(preview.plates, library_rows)

            for plan in fresh.plates:
                plate_id = PlateId(plan.plate_id)
                plate_row = metadata_by_id[plan.plate_id]
                plate_custom = json_object(plate_row.get("plate_custom_json", {}))
                previous_registry = json_object(plate_custom.get("cultivation_registry", {}))
                next_registry = {**previous_registry, **json_object(plan.registry)}
                if "CultivationConcentrationDecimalPlaces" in plan.registry:
                    next_registry.pop("CultivationConcentrationSignificantFigures", None)
                plate_custom["cultivation_registry"] = next_registry

                current_rows = rows_by_plate.get(plan.plate_id, [])
                by_position = {str(row["position"]).upper(): row for row in current_rows}
                changes: list[dict[str, object]] = []
                audit: list[dict[str, object]] = []
                for assignment in plan.assignments:
                    position = str(assignment.get("Well", "")).upper()
                    current_row = by_position.get(position)
                    if current_row is None:
                        raise _error(
                            "Planned well is missing from the selected plate.", position=position
                        )
                    custom = json_object(current_row.get("custom_json", {}))
                    planned = {key: value for key, value in assignment.items() if key != "Well"}
                    if planned.get("InternalCultivationID") != current_row.get("well_id"):
                        raise _error("Planned well identity changed.", position=position)
                    before_id = _nonempty_text(custom.get("Cultivation"))
                    after_id = _nonempty_text(planned.get("Cultivation"))
                    if before_id and before_id != after_id:
                        if custom.get("CultivationNumberingScheme") == SCHEME:
                            history = planned.get("PreviousCultivationIDs", [])
                            if (
                                not preview.settings.reassign_changed_conditions
                                or not isinstance(history, list)
                                or before_id not in history
                            ):
                                raise _error(
                                    "Saved cultivation IDs cannot be reassigned without "
                                    "an explicit reviewed plan.",
                                    position=position,
                                )
                        # A legacy ID on a newly blank/unidentified well is retired,
                        # never left as the current external identity.
                        if not after_id:
                            planned["Cultivation"] = ""
                        history = custom.get("PreviousCultivationIDs", [])
                        if not isinstance(history, list) or any(
                            not isinstance(item, str) for item in history
                        ):
                            raise _error(
                                "Invalid previous cultivation ID history.", position=position
                            )
                        if before_id not in history:
                            planned["PreviousCultivationIDs"] = [*history, before_id]
                    merged = {**custom, **planned}
                    if "CultivationConcentrationDecimalPlaces" in planned:
                        merged.pop("CultivationConcentrationSignificantFigures", None)
                    if merged == custom:
                        continue
                    changes.append({"position": position, "custom_json": merged})
                    audit.append(
                        {
                            "position": position,
                            "internal_id": str(current_row["well_id"]),
                            "before": before_id,
                            "after": after_id,
                            "previous_ids": merged.get("PreviousCultivationIDs", []),
                        }
                    )

                if not changes and next_registry == previous_registry:
                    continue
                self.repository.update_plate_metadata(
                    plate_id,
                    str(plate_row["updated_at"]),
                    {"custom_json": plate_custom} if next_registry != previous_registry else {},
                )
                if changes:
                    self.repository.update_well_layout(plate_id, changes)
                self.repository.append_provenance(
                    {
                        "actor_id": actor_id,
                        "event_type": "cultivation_registry_saved",
                        "entity_type": "plate",
                        "entity_id": plate_id,
                        "details_json": {
                            "scheme": SCHEME,
                            "plate_number": plan.plate_number,
                            "settings": {
                                "team_code": preview.settings.team_code,
                                "system_code": preview.settings.system_code,
                                "condition_fields": list(preview.settings.condition_fields),
                                "concentration_significant_figures": (
                                    preview.settings.concentration_significant_figures
                                ),
                                "concentration_decimal_places": (
                                    preview.settings.concentration_decimal_places
                                ),
                                "concentration_exact": preview.settings.concentration_exact,
                                "reassign_changed_conditions": (
                                    preview.settings.reassign_changed_conditions
                                ),
                            },
                            "registry": {"before": previous_registry, "after": next_registry},
                            "cultivations": audit,
                        },
                    }
                )
                changed_ids.append(plate_id)
        return tuple(changed_ids)


def _same_preview(before: RegistryPreview, after: RegistryPreview) -> bool:
    return (
        before.plate_ids == after.plate_ids
        and before.settings == after.settings
        and before.expected_versions == after.expected_versions
        and _stable_json(before.plates) == _stable_json(after.plates)
    )


def _stable_json(value: object) -> str:
    if isinstance(value, tuple):
        value = [asdict(item) for item in value]
    return json.dumps(value, sort_keys=True)


def _validate_planned_id_collisions(
    plans: tuple[RegistryPlatePlan, ...], rows: tuple[dict[str, object], ...]
) -> None:
    owners: dict[str, str] = {}
    targets = {
        (plan.plate_id, str(item.get("Well", "")).upper())
        for plan in plans
        for item in plan.assignments
    }
    for row in rows:
        custom = json_object(row.get("custom_json", {}))
        cultivation_id = _nonempty_text(custom.get("Cultivation"))
        owner = str(row["well_id"])
        if cultivation_id and (str(row["plate_id"]), str(row["position"]).upper()) not in targets:
            if cultivation_id in owners and owners[cultivation_id] != owner:
                raise _error(
                    "Duplicate saved cultivation ID in library.", cultivation=cultivation_id
                )
            owners[cultivation_id] = owner
    for plan in plans:
        for item in plan.assignments:
            cultivation_id = _nonempty_text(item.get("Cultivation"))
            if not cultivation_id:
                continue
            owner = str(item.get("InternalCultivationID", ""))
            if cultivation_id in owners and owners[cultivation_id] != owner:
                raise _error(
                    "Planned cultivation ID collides with a saved ID.", cultivation=cultivation_id
                )
            owners[cultivation_id] = owner


def _nonempty_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validated_plate_ids(plate_ids: Sequence[PlateId]) -> tuple[PlateId, ...]:
    requested = tuple(plate_ids)
    if not requested or len(requested) > _MAX_PLATES:
        raise _error("Select between 1 and 500 Growth plates.")
    if any(not isinstance(plate_id, str) or not plate_id.strip() for plate_id in requested):
        raise _error("Growth plate IDs must be non-empty strings.")
    if len(set(requested)) != len(requested):
        raise _error("Growth plate IDs must be unique.")
    return requested


def _error(message: str, **context: object) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message, **context))
