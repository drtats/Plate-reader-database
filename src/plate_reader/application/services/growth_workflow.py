"""Growth workflow use cases beyond the atomic source import."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    ComputeGrowthBackgroundRevision,
    ExperimentId,
    ExportPortableRun,
    PlateId,
    RevisionId,
    Role,
    SearchRuns,
    UpdatePlateMetadata,
    UpdateWellLayout,
    WellLayoutChange,
)
from plate_reader.application.ports.repositories import PlateSnapshot, RunSummary
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.source_limits import (
    MAX_GROWTH_SOURCE_BYTES,
    source_bytes_within_limit,
)
from plate_reader.domain.common.errors import DomainIssue
from plate_reader.domain.common.plate import WellPosition
from plate_reader.domain.growth import (
    GROWTH_BACKGROUND_VERSION,
    GrowthMeasurement,
    NormalizationSettings,
    WellBackgroundAssignment,
    calculate_backgrounds,
    parse_growth_csv,
    parse_label_layout,
)


class GrowthWorkflowRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...

    def plate_cache_token(self, plate_id: PlateId) -> str | None: ...

    def search_runs(self, filters: dict[str, object]) -> Sequence[RunSummary]: ...

    def update_plate_metadata(
        self, plate_id: PlateId, expected_updated_at: str, changes: dict[str, object]
    ) -> str: ...

    def update_experiment_metadata(
        self,
        experiment_id: ExperimentId,
        expected_updated_at: str,
        changes: dict[str, object],
    ) -> str: ...

    def update_well_layout(
        self, plate_id: PlateId, changes: Sequence[dict[str, object]]
    ) -> None: ...

    def add_analysis_revision(self, values: dict[str, object]) -> RevisionId: ...

    def insert_growth_backgrounds(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...

    def growth_backgrounds(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]: ...

    def provenance_for_plate(self, plate_id: PlateId) -> tuple[dict[str, object], ...]: ...


class PortableRunExporter(Protocol):
    def export(
        self, plate_ids: Sequence[PlateId], revision_ids: Sequence[RevisionId]
    ) -> tuple[str, bytes]: ...


@dataclass(frozen=True, slots=True)
class GrowthPreview:
    source_sha256: str
    measurement_count: int
    well_count: int
    timepoint_count: int
    first_elapsed_minutes: float
    last_elapsed_minutes: float
    label_count: int
    issues: tuple[DomainIssue, ...]


@dataclass(frozen=True, slots=True)
class BackgroundRevisionResult:
    revision_id: RevisionId
    background_count: int
    issues: tuple[DomainIssue, ...]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class GrowthRunView:
    snapshot: PlateSnapshot
    backgrounds: tuple[dict[str, object], ...]
    provenance: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class PortableArtifact:
    filename: str
    content: bytes


class PreviewGrowthRunService:
    def execute(
        self,
        csv_text: str,
        *,
        label_csv_text: str | None = None,
        fallback_interval_minutes: float = 10.0,
        t0_offset_minutes: float = 0.0,
    ) -> GrowthPreview:
        source_bytes = source_bytes_within_limit(
            csv_text, max_bytes=MAX_GROWTH_SOURCE_BYTES, kind="Growth CSV"
        )
        normalized = parse_growth_csv(
            csv_text,
            NormalizationSettings(
                interval_minutes=fallback_interval_minutes,
                t0_offset_minutes=t0_offset_minutes,
            ),
        )
        labels = parse_label_layout(label_csv_text) if label_csv_text is not None else ()
        return GrowthPreview(
            source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            measurement_count=len(normalized.measurements),
            well_count=len(normalized.positions),
            timepoint_count=len(normalized.timepoints_microseconds),
            first_elapsed_minutes=normalized.timepoints_microseconds[0] / 60_000_000,
            last_elapsed_minutes=normalized.timepoints_microseconds[-1] / 60_000_000,
            label_count=len(labels),
            issues=normalized.issues,
        )


class UpdateGrowthMetadataService:
    def __init__(self, repository: GrowthWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: UpdatePlateMetadata) -> PlateSnapshot:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        snapshot = _growth_snapshot(self.repository, command.plate_id)
        experiment_changes = _present_values(
            {"name": command.experiment_name, "project": command.project, "notes": command.notes}
        )
        plate_changes = _present_values(
            {
                "plate_name": command.plate_name,
                "instrument": command.instrument,
                "lifecycle_status": command.lifecycle_status,
            }
        )
        if not experiment_changes and not plate_changes:
            raise ValueError("At least one metadata field must be changed")
        experiment_id = ExperimentId(str(snapshot.metadata["experiment_id"]))
        with self.repository.transaction():
            if experiment_changes:
                self.repository.update_experiment_metadata(
                    experiment_id,
                    str(snapshot.metadata["experiment_updated_at"]),
                    experiment_changes,
                )
            self.repository.update_plate_metadata(
                command.plate_id, command.expected_updated_at, plate_changes
            )
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "growth_metadata_updated",
                    "entity_type": "plate",
                    "entity_id": command.plate_id,
                    "details_json": {
                        "experiment_fields": sorted(experiment_changes),
                        "plate_fields": sorted(plate_changes),
                    },
                }
            )
        return _growth_snapshot(self.repository, command.plate_id)


class UpdateGrowthLayoutService:
    def __init__(self, repository: GrowthWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: UpdateWellLayout) -> PlateSnapshot:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        _growth_snapshot(self.repository, command.plate_id)
        changes = [
            {key: value for key, value in _record_values(change).items() if value is not None}
            for change in command.changes
        ]
        if not changes:
            raise ValueError("At least one well layout change is required")
        with self.repository.transaction():
            self.repository.update_plate_metadata(command.plate_id, command.expected_updated_at, {})
            self.repository.update_well_layout(command.plate_id, changes)
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "growth_layout_updated",
                    "entity_type": "plate",
                    "entity_id": command.plate_id,
                    "details_json": {"positions": [change["position"] for change in changes]},
                }
            )
        return _growth_snapshot(self.repository, command.plate_id)


class ComputeGrowthBackgroundService:
    def __init__(
        self,
        repository: GrowthWorkflowRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(self, command: ComputeGrowthBackgroundRevision) -> BackgroundRevisionResult:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        if command.algorithm_version != GROWTH_BACKGROUND_VERSION:
            raise ValueError(f"Unsupported growth background version: {command.algorithm_version}")
        snapshot = _growth_snapshot(self.repository, command.plate_id)
        position_by_well = {
            str(well["well_id"]): WellPosition.parse(str(well["position"]))
            for well in snapshot.wells
        }
        measurements = tuple(
            GrowthMeasurement(
                position_by_well[str(row["well_id"])],
                _as_int(row["time_index"]),
                _as_int(row["elapsed_microseconds"]),
                str(row["channel"]),
                _as_float(row["value_raw"]),
            )
            for row in snapshot.raw_observations
        )
        assignments = tuple(
            WellBackgroundAssignment(
                WellPosition.parse(str(well["position"])),
                bool(well["is_blank"]),
                str(well["background_group"] or "plate"),
            )
            for well in snapshot.wells
        )
        input_sha256 = _background_input_hash(snapshot)
        result = calculate_backgrounds(measurements, assignments)
        revision_id = RevisionId(self.id_factory())
        with self.repository.transaction():
            self.repository.add_analysis_revision(
                {
                    "revision_id": revision_id,
                    "plate_id": command.plate_id,
                    "assay_type": AssayType.GROWTH,
                    "algorithm_name": "growth_background",
                    "algorithm_version": command.algorithm_version,
                    "parameters_json": command.parameters,
                    "input_sha256": input_sha256,
                    "created_by": actor_id,
                }
            )
            self.repository.insert_growth_backgrounds(
                revision_id,
                [
                    {
                        "background_group": row.background_group,
                        "channel": row.channel,
                        "time_index": row.time_index,
                        "elapsed_microseconds": row.elapsed_microseconds,
                        "mean_value": row.mean_value,
                        "std_value": row.std_value,
                        "coefficient_of_variation": row.coefficient_of_variation,
                        "blank_count": row.blank_count,
                        "qc_status": row.qc_status,
                    }
                    for row in result.backgrounds
                ],
            )
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "growth_background_computed",
                    "entity_type": "revision",
                    "entity_id": revision_id,
                    "details_json": {
                        "plate_id": command.plate_id,
                        "input_sha256": input_sha256,
                        "background_count": len(result.backgrounds),
                    },
                }
            )
        return BackgroundRevisionResult(
            revision_id, len(result.backgrounds), result.issues, input_sha256
        )


class SearchGrowthRunsService:
    def __init__(self, repository: GrowthWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, query: SearchRuns) -> Sequence[RunSummary]:
        allowed = {Role.VIEWER, Role.EDITOR, Role.ADMIN}
        require_role(self.repository, query.actor, allowed)
        if query.include_deleted and query.actor.role is not Role.ADMIN:
            raise PermissionError("Only admins may search deleted runs")
        return self.repository.search_runs(
            {
                "text": query.text,
                "assay_type": query.assay_type or AssayType.GROWTH,
                "project": query.project,
                "strain": query.strain,
                "medium": query.medium,
                "treatment": query.treatment,
                "date_from": query.date_from.isoformat() if query.date_from else None,
                "date_to": query.date_to.isoformat() if query.date_to else None,
                "include_deleted": query.include_deleted,
                "limit": query.limit,
                "offset": query.offset,
            }
        )


class LoadGrowthRunService:
    def __init__(self, repository: GrowthWorkflowRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, plate_id: PlateId, revision_id: RevisionId | None = None
    ) -> GrowthRunView:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        snapshot = _growth_snapshot(self.repository, plate_id)
        selected_revision = revision_id or _current_background_revision(snapshot)
        backgrounds = (
            self.repository.growth_backgrounds(selected_revision) if selected_revision else ()
        )
        return GrowthRunView(snapshot, backgrounds, self.repository.provenance_for_plate(plate_id))

    def cache_token(self, actor: Actor, plate_id: PlateId) -> str:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        token = self.repository.plate_cache_token(plate_id)
        if token is None:
            raise LookupError(f"Growth plate not found: {plate_id}")
        return token


class ExportGrowthRunService:
    def __init__(self, repository: GrowthWorkflowRepository, exporter: PortableRunExporter) -> None:
        self.repository = repository
        self.exporter = exporter

    def execute(self, command: ExportPortableRun) -> PortableArtifact:
        require_role(self.repository, command.actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        for plate_id in command.plate_ids:
            _growth_snapshot(self.repository, plate_id)
        filename, content = self.exporter.export(command.plate_ids, command.revision_ids)
        return PortableArtifact(filename, content)


def _growth_snapshot(repository: GrowthWorkflowRepository, plate_id: PlateId) -> PlateSnapshot:
    snapshot = repository.load_plate(plate_id)
    if snapshot is None:
        raise LookupError(f"Growth plate not found: {plate_id}")
    if str(snapshot.metadata["assay_type"]) != AssayType.GROWTH:
        raise ValueError(f"Plate is not a growth run: {plate_id}")
    return snapshot


def _present_values(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _record_values(record: WellLayoutChange) -> dict[str, object]:
    return {
        "position": record.position,
        "display_name": record.display_name,
        "is_blank": record.is_blank,
        "background_group": record.background_group,
        "strain": record.strain,
        "medium": record.medium,
        "treatment": record.treatment,
        "concentration": record.concentration,
        "concentration_unit": record.concentration_unit,
        "replicate": record.replicate,
    }


def _background_input_hash(snapshot: PlateSnapshot) -> str:
    payload = {
        "raw": sorted(
            (
                str(row["well_id"]),
                str(row["channel"]),
                _as_int(row["time_index"]),
                _as_int(row["elapsed_microseconds"]),
                _as_float(row["value_raw"]),
            )
            for row in snapshot.raw_observations
        ),
        "assignments": sorted(
            (
                str(well["position"]),
                bool(well["is_blank"]),
                str(well["background_group"] or "plate"),
            )
            for well in snapshot.wells
        ),
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _current_background_revision(snapshot: PlateSnapshot) -> RevisionId | None:
    for revision in reversed(snapshot.revisions):
        if revision["algorithm_name"] == "growth_background" and bool(revision["is_current"]):
            return RevisionId(str(revision["revision_id"]))
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer database value")
    return value


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expected a numeric database value")
    return float(value)
