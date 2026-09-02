"""Universal custom-column definitions for assay layout editors and exports."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from plate_reader.application.contracts import Actor, AssayType, Role
from plate_reader.application.services.authorization import require_role

_OPTION_TYPE_PREFIX = "layout_column:"

# These names are already owned by an editor or Growth tabular-export contract.
# Matching is case-insensitive so a custom field cannot create an ambiguous CSV.
_COMMON_RESERVED_NAMES = {
    "Well",
    "Display name",
    "Blank",
    "Strain",
    "Concentration",
    "Concentration unit",
    "Media",
    "Replicate",
    "Notes",
}
_GROWTH_RESERVED_NAMES = {
    *_COMMON_RESERVED_NAMES,
    "Raw label",
    "Background group",
    "Plot",
    "Group",
    "Inoculum size",
    "Inoculum unit",
    "Treatment",
    "T0 added (min)",
    "Cultivation Short ID",
    "Date Time",
    "Culture Age H",
    "Well Row",
    "Well Column",
    "Culture Volume uL",
    "Condition 1 State",
    "Condition 2 State",
    "Condition 3 State",
    "Background Subtracted OD",
    "Microplate ID",
    "Background Mean OD",
    "Background SD OD",
    "Background Blank N",
    "Background QC Flag",
    "Background QC Reason",
    "Run ID",
    "Project",
    "Experiment Name",
    "Time Min",
    "Signal Type",
    "Raw OD",
    "BG Group",
    "Metadata Level",
    "Experiment Date",
    "User",
    "Instrument",
    "Temperature",
    "Source Folder",
    "Editable Metadata JSON",
    "Source Metadata JSON",
    "run_id",
    "well",
    "display_name",
    "media",
    "strain",
    "inoculum_size",
    "treatments",
    "is_blank",
    "bg_group",
    "row",
    "col",
    "raw_label",
    "plot",
    "group",
    "replicate",
    "notes",
    "treatment_1",
    "conc_1",
    "unit_1",
    "t0_added_min",
}
_MIC_RESERVED_NAMES = {
    *_COMMON_RESERVED_NAMES,
    "Raw OD",
    "Antibiotic / treatment",
}


class LayoutColumnReadRepository(Protocol):
    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def list_saved_options(
        self, option_type: str | None = None
    ) -> tuple[dict[str, object], ...]: ...


class LayoutColumnRepository(LayoutColumnReadRepository, Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def save_saved_option(self, values: dict[str, object]) -> bool: ...

    def delete_saved_option(self, option_type: str, value: str) -> None: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...


@dataclass(frozen=True, slots=True)
class LayoutColumn:
    assay_type: AssayType
    name: str
    created_by: str
    created_at: str


class ListLayoutColumnsService:
    def __init__(self, repository: LayoutColumnReadRepository) -> None:
        self.repository = repository

    def execute(self, actor: Actor, assay_type: AssayType) -> tuple[LayoutColumn, ...]:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        return tuple(
            LayoutColumn(
                assay_type,
                str(row["value"]),
                str(row["created_by"]),
                str(row["created_at"]),
            )
            for row in self.repository.list_saved_options(_option_type(assay_type))
        )


class SaveLayoutColumnService:
    def __init__(self, repository: LayoutColumnRepository) -> None:
        self.repository = repository

    def execute(self, actor: Actor, assay_type: AssayType, name: str) -> LayoutColumn:
        actor_id = require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
        normalized = _validated_name(assay_type, name)
        option_type = _option_type(assay_type)
        with self.repository.transaction():
            created = self.repository.save_saved_option(
                {"option_type": option_type, "value": normalized, "created_by": actor_id}
            )
            if created:
                self.repository.append_provenance(
                    {
                        "actor_id": actor_id,
                        "event_type": "layout_column_added",
                        "entity_type": "layout_column",
                        "entity_id": f"{assay_type.value}:{normalized}",
                    }
                )
        return next(
            column
            for column in ListLayoutColumnsService(self.repository).execute(actor, assay_type)
            if column.name.casefold() == normalized.casefold()
        )


class DeleteLayoutColumnService:
    def __init__(self, repository: LayoutColumnRepository) -> None:
        self.repository = repository

    def execute(self, actor: Actor, assay_type: AssayType, name: str) -> None:
        actor_id = require_role(self.repository, actor, {Role.EDITOR, Role.ADMIN})
        normalized = _validated_name(assay_type, name)
        with self.repository.transaction():
            self.repository.delete_saved_option(_option_type(assay_type), normalized)
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "layout_column_deleted",
                    "entity_type": "layout_column",
                    "entity_id": f"{assay_type.value}:{normalized}",
                }
            )


def _option_type(assay_type: AssayType) -> str:
    return f"{_OPTION_TYPE_PREFIX}{assay_type.value}"


def _validated_name(assay_type: AssayType, name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Custom layout column name cannot be empty")
    if len(normalized) > 100:
        raise ValueError("Custom layout column name cannot exceed 100 characters")
    if any(character in normalized for character in ("\n", "\r", "\t")):
        raise ValueError("Custom layout column name cannot contain control characters")
    reserved = _GROWTH_RESERVED_NAMES if assay_type is AssayType.GROWTH else _MIC_RESERVED_NAMES
    if normalized.casefold() in {item.casefold() for item in reserved}:
        raise ValueError(f"{normalized} is a reserved layout or export column name")
    return normalized
