"""Reusable plate-template and controlled-option application services."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    DeleteOption,
    DeletePlateTemplate,
    Role,
    SaveOption,
    SavePlateTemplate,
)
from plate_reader.application.services.authorization import require_role
from plate_reader.domain.common.plate import PLATE_96


class SupportingDataRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def user_by_email(self, email: str) -> dict[str, object] | None: ...

    def list_plate_templates(
        self, assay_type: AssayType | None = None
    ) -> tuple[dict[str, object], ...]: ...

    def save_plate_template(self, values: dict[str, object]) -> str: ...

    def delete_plate_template(self, template_id: str, expected_updated_at: str) -> None: ...

    def list_saved_options(
        self, option_type: str | None = None
    ) -> tuple[dict[str, object], ...]: ...

    def save_saved_option(self, values: dict[str, object]) -> bool: ...

    def delete_saved_option(self, option_type: str, value: str) -> None: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...


@dataclass(frozen=True, slots=True)
class PlateTemplate:
    template_id: str
    template_name: str
    assay_type: AssayType
    layout: tuple[dict[str, object], ...]
    created_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SavedOption:
    option_type: str
    value: str
    created_by: str
    created_at: str


class ListPlateTemplatesService:
    def __init__(self, repository: SupportingDataRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, assay_type: AssayType | None = None
    ) -> tuple[PlateTemplate, ...]:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        return tuple(_template(row) for row in self.repository.list_plate_templates(assay_type))


class SavePlateTemplateService:
    def __init__(
        self,
        repository: SupportingDataRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(self, command: SavePlateTemplate) -> PlateTemplate:
        actor_id = require_role(self.repository, command.actor, {Role.ADMIN})
        name = command.template_name.strip()
        if not name:
            raise ValueError("Template name cannot be empty")
        layout = _validated_layout(command.layout)
        template_id = command.template_id or self.id_factory()
        with self.repository.transaction():
            self.repository.save_plate_template(
                {
                    "template_id": template_id,
                    "template_name": name,
                    "assay_type": command.assay_type,
                    "layout": layout,
                    "created_by": actor_id,
                    "expected_updated_at": command.expected_updated_at,
                }
            )
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "plate_template_saved",
                    "entity_type": "plate_template",
                    "entity_id": template_id,
                    "details_json": {
                        "template_name": name,
                        "assay_type": command.assay_type,
                        "updated": command.expected_updated_at is not None,
                    },
                }
            )
        return next(
            template
            for template in ListPlateTemplatesService(self.repository).execute(
                command.actor, command.assay_type
            )
            if template.template_id == template_id
        )


class DeletePlateTemplateService:
    def __init__(self, repository: SupportingDataRepository) -> None:
        self.repository = repository

    def execute(self, command: DeletePlateTemplate) -> None:
        actor_id = require_role(self.repository, command.actor, {Role.ADMIN})
        with self.repository.transaction():
            self.repository.delete_plate_template(command.template_id, command.expected_updated_at)
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "plate_template_deleted",
                    "entity_type": "plate_template",
                    "entity_id": command.template_id,
                }
            )


class ListSavedOptionsService:
    def __init__(self, repository: SupportingDataRepository) -> None:
        self.repository = repository

    def execute(self, actor: Actor, option_type: str | None = None) -> tuple[SavedOption, ...]:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        return tuple(_option(row) for row in self.repository.list_saved_options(option_type))


class SaveOptionService:
    def __init__(self, repository: SupportingDataRepository) -> None:
        self.repository = repository

    def execute(self, command: SaveOption) -> SavedOption:
        actor_id = require_role(self.repository, command.actor, {Role.ADMIN})
        option_type = command.option_type.strip()
        value = command.value.strip()
        if not option_type or not value:
            raise ValueError("Saved option type and value cannot be empty")
        with self.repository.transaction():
            created = self.repository.save_saved_option(
                {"option_type": option_type, "value": value, "created_by": actor_id}
            )
            if created:
                self.repository.append_provenance(
                    {
                        "actor_id": actor_id,
                        "event_type": "saved_option_added",
                        "entity_type": "saved_option",
                        "entity_id": f"{option_type}:{value}",
                    }
                )
        return next(
            option
            for option in ListSavedOptionsService(self.repository).execute(
                command.actor, option_type
            )
            if option.value.casefold() == value.casefold()
        )


class DeleteOptionService:
    def __init__(self, repository: SupportingDataRepository) -> None:
        self.repository = repository

    def execute(self, command: DeleteOption) -> None:
        actor_id = require_role(self.repository, command.actor, {Role.ADMIN})
        with self.repository.transaction():
            self.repository.delete_saved_option(command.option_type, command.value)
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "saved_option_deleted",
                    "entity_type": "saved_option",
                    "entity_id": f"{command.option_type}:{command.value}",
                }
            )


def _validated_layout(layout: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    expected = tuple(position.label for position in PLATE_96.positions())
    positions = tuple(str(row.get("position", "")) for row in layout)
    if len(layout) != 96 or set(positions) != set(expected):
        raise ValueError("A plate template must contain each A1-H12 position exactly once")
    by_position = {str(row["position"]): dict(row) for row in layout}
    ordered = tuple(by_position[position] for position in expected)
    json.dumps(ordered, ensure_ascii=False, allow_nan=False)
    return ordered


def _template(row: Mapping[str, object]) -> PlateTemplate:
    layout = json.loads(str(row["layout_json"]))
    if not isinstance(layout, list) or not all(isinstance(item, dict) for item in layout):
        raise ValueError("Stored plate template layout is invalid")
    return PlateTemplate(
        str(row["template_id"]),
        str(row["template_name"]),
        AssayType(str(row["assay_type"])),
        tuple({str(key): value for key, value in item.items()} for item in layout),
        str(row["created_by"]),
        str(row["created_at"]),
        str(row["updated_at"]),
    )


def _option(row: Mapping[str, object]) -> SavedOption:
    return SavedOption(
        str(row["option_type"]),
        str(row["value"]),
        str(row["created_by"]),
        str(row["created_at"]),
    )
