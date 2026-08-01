"""Transactional and idempotent growth CSV import use case."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from plate_reader.application.contracts import (
    AssayType,
    ExperimentId,
    ImportGrowthRun,
    PlateId,
    Role,
    WellLayoutChange,
)
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.source_limits import (
    MAX_GROWTH_SOURCE_BYTES,
    source_bytes_within_limit,
)
from plate_reader.domain.common.errors import DomainIssue
from plate_reader.domain.common.plate import PLATE_96, WellPosition
from plate_reader.domain.growth import (
    GROWTH_NORMALIZATION_VERSION,
    NormalizationSettings,
    parse_growth_csv,
    parse_label_layout,
)


class SourceHashMismatchError(ValueError):
    pass


class ImportAuthorizationError(PermissionError):
    pass


class UnsupportedParserVersionError(ValueError):
    pass


class GrowthImportRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def user_by_email(self, email: str) -> dict[str, object] | None: ...

    def upsert_user(self, values: Mapping[str, object]) -> str: ...

    def plate_for_source(self, idempotency_key: str) -> PlateId | None: ...

    def create_experiment(self, values: dict[str, object]) -> ExperimentId: ...

    def create_plate(self, values: dict[str, object]) -> PlateId: ...

    def insert_wells(self, plate_id: PlateId, rows: Sequence[dict[str, object]]) -> None: ...

    def insert_conditions(self, rows: Sequence[dict[str, object]]) -> None: ...

    def insert_raw_observations(
        self, plate_id: PlateId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def record_import_source(self, values: Mapping[str, object]) -> str: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...


@dataclass(frozen=True, slots=True)
class GrowthImportResult:
    experiment_id: ExperimentId
    plate_id: PlateId
    created: bool
    idempotency_key: str
    measurement_count: int
    issues: tuple[DomainIssue, ...]


class ImportGrowthRunService:
    def __init__(
        self,
        repository: GrowthImportRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(
        self,
        command: ImportGrowthRun,
        csv_text: str,
        *,
        label_csv_text: str | None = None,
        layout_changes: Sequence[WellLayoutChange] = (),
    ) -> GrowthImportResult:
        if command.actor.role not in {Role.EDITOR, Role.ADMIN}:
            raise ImportAuthorizationError("Growth import requires editor or admin role")
        if command.parser_version != GROWTH_NORMALIZATION_VERSION:
            raise UnsupportedParserVersionError(
                f"Unsupported growth parser version: {command.parser_version}"
            )
        source_bytes = source_bytes_within_limit(
            csv_text, max_bytes=MAX_GROWTH_SOURCE_BYTES, kind="Growth CSV"
        )
        actual_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_hash != command.source_sha256.casefold():
            raise SourceHashMismatchError(
                f"Growth source hash mismatch: expected {command.source_sha256}, got {actual_hash}"
            )
        idempotency_key = command.idempotency_key or (
            f"growth_csv:{actual_hash}:{command.parser_version}"
        )
        existing_plate = self.repository.plate_for_source(idempotency_key)
        if existing_plate is not None:
            snapshot = self.repository.load_plate(existing_plate)
            if snapshot is None:
                raise RuntimeError("Imported source points to a missing plate")
            return GrowthImportResult(
                experiment_id=ExperimentId(str(snapshot.metadata["experiment_id"])),
                plate_id=existing_plate,
                created=False,
                idempotency_key=idempotency_key,
                measurement_count=len(snapshot.raw_observations),
                issues=(),
            )

        normalized = parse_growth_csv(
            csv_text,
            NormalizationSettings(
                t0_offset_minutes=command.t0_offset_minutes,
                interval_minutes=command.fallback_interval_minutes,
            ),
        )
        labels = (
            {label.position: label.label for label in parse_label_layout(label_csv_text)}
            if label_csv_text is not None
            else {}
        )
        layout = self._validated_layout(layout_changes)
        experiment_id = ExperimentId(self.id_factory())
        plate_id = PlateId(self.id_factory())
        well_ids = {position: self.id_factory() for position in PLATE_96.positions()}
        with self.repository.transaction():
            actor_id = self._ensure_actor(command)
            self.repository.create_experiment(
                {
                    "experiment_id": experiment_id,
                    "name": command.experiment_name,
                    "experiment_date": command.experiment_date.isoformat(),
                    "operator_name": command.actor.email,
                    "created_by": actor_id,
                }
            )
            self.repository.create_plate(
                {
                    "plate_id": plate_id,
                    "experiment_id": experiment_id,
                    "assay_type": AssayType.GROWTH,
                    "plate_name": command.plate_name,
                    "plate_format": 96,
                    "channel": normalized.measurements[0].channel,
                    "created_by": actor_id,
                }
            )
            self.repository.insert_wells(
                plate_id,
                [
                    {
                        "well_id": well_ids[position],
                        "position": position.label,
                        "row_index": position.row_index,
                        "column_index": position.column_index,
                        "raw_label": labels.get(position),
                        "display_name": (
                            layout[position].display_name
                            if position in layout and layout[position].display_name is not None
                            else labels.get(position)
                        ),
                        "is_blank": layout[position].is_blank or False
                        if position in layout
                        else False,
                        "background_group": (
                            layout[position].background_group or "plate"
                            if position in layout
                            else "plate"
                        ),
                    }
                    for position in PLATE_96.positions()
                ],
            )
            self.repository.insert_conditions(
                [
                    {
                        "well_id": well_ids[position],
                        "strain": layout[position].strain if position in layout else None,
                        "medium": layout[position].medium if position in layout else None,
                        "treatment": layout[position].treatment if position in layout else None,
                        "concentration": (
                            layout[position].concentration if position in layout else None
                        ),
                        "concentration_unit": (
                            layout[position].concentration_unit if position in layout else None
                        ),
                        "replicate": layout[position].replicate or 1 if position in layout else 1,
                    }
                    for position in PLATE_96.positions()
                ]
            )
            self.repository.insert_raw_observations(
                plate_id,
                [
                    {
                        "well_id": well_ids[measurement.position],
                        "channel": measurement.channel,
                        "time_index": measurement.time_index,
                        "elapsed_microseconds": measurement.elapsed_microseconds,
                        "value_raw": measurement.value_raw,
                    }
                    for measurement in normalized.measurements
                ],
            )
            source_id = self.repository.record_import_source(
                {
                    "source_id": self.id_factory(),
                    "plate_id": plate_id,
                    "source_kind": "growth_csv",
                    "original_filename": command.source_name,
                    "content_sha256": actual_hash,
                    "byte_size": len(source_bytes),
                    "parser_version": command.parser_version,
                    "idempotency_key": idempotency_key,
                    "status": "imported",
                    "imported_by": actor_id,
                }
            )
            self.repository.append_provenance(
                {
                    "event_id": self.id_factory(),
                    "actor_id": actor_id,
                    "event_type": "growth_imported",
                    "entity_type": "plate",
                    "entity_id": plate_id,
                    "details_json": {
                        "source_id": source_id,
                        "content_sha256": actual_hash,
                        "parser_version": command.parser_version,
                        "normalization_algorithm": GROWTH_NORMALIZATION_VERSION,
                    },
                }
            )
        return GrowthImportResult(
            experiment_id=experiment_id,
            plate_id=plate_id,
            created=True,
            idempotency_key=idempotency_key,
            measurement_count=len(normalized.measurements),
            issues=normalized.issues,
        )

    def _ensure_actor(self, command: ImportGrowthRun) -> str:
        existing = self.repository.user_by_email(command.actor.email)
        if existing is not None:
            if not bool(existing["is_active"]):
                raise ImportAuthorizationError("Inactive users cannot import data")
            if str(existing["user_id"]) != command.actor.user_id:
                raise ImportAuthorizationError("Authenticated identity does not match stored user")
            stored_role = Role(str(existing["role"]))
            if stored_role not in {Role.EDITOR, Role.ADMIN}:
                raise ImportAuthorizationError("Stored user role cannot import data")
            return str(existing["user_id"])
        return self.repository.upsert_user(
            {
                "user_id": command.actor.user_id,
                "email": command.actor.email,
                "display_name": command.actor.email.split("@", maxsplit=1)[0],
                "role": command.actor.role,
                "is_active": True,
            }
        )

    @staticmethod
    def _validated_layout(
        changes: Sequence[WellLayoutChange],
    ) -> dict[WellPosition, WellLayoutChange]:
        result: dict[WellPosition, WellLayoutChange] = {}
        for change in changes:
            position = WellPosition.parse(change.position, PLATE_96)
            if position in result:
                raise ValueError(f"Duplicate layout change: {position.label}")
            result[position] = change
        return result
