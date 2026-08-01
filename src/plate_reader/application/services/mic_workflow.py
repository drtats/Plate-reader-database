"""MIC load, edit, revision, search, review, lock, and soft-delete use cases."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    ComputeMicRevision,
    ExperimentId,
    ExportPortableRun,
    MicWellLayoutChange,
    PlateId,
    RevisionId,
    Role,
    SearchMicResults,
    SearchRuns,
    SetMicLockState,
    SetMicReviewState,
    SoftDeleteMicPlate,
    UpdateMicLayout,
    UpdateMicMetadata,
)
from plate_reader.application.ports.repositories import PlateSnapshot, RunSummary
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_workflow import PortableArtifact, PortableRunExporter
from plate_reader.application.services.mic_common import (
    mic_input_sha256,
    mic_wells_from_snapshot,
    persist_mic_analysis,
    well_ids_from_snapshot,
)
from plate_reader.domain.common.errors import DomainIssue
from plate_reader.domain.mic import MIC_ENDPOINT_VERSION, analyze_mic_endpoint


class MicWorkflowRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...

    def plate_cache_token(self, plate_id: PlateId) -> str | None: ...

    def search_runs(self, filters: dict[str, object]) -> Sequence[RunSummary]: ...

    def search_mic_results(
        self, filters: Mapping[str, object]
    ) -> tuple[dict[str, object], ...]: ...

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

    def insert_mic_well_calls(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def insert_mic_results(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def mic_well_calls(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]: ...

    def mic_results(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...

    def provenance_for_plate(self, plate_id: PlateId) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True, slots=True)
class MicRevisionResult:
    revision_id: RevisionId
    call_count: int
    result_count: int
    issues: tuple[DomainIssue, ...]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class MicPlateView:
    snapshot: PlateSnapshot
    well_calls: tuple[dict[str, object], ...]
    results: tuple[dict[str, object], ...]
    provenance: tuple[dict[str, object], ...]


class ComputeMicRevisionService:
    def __init__(
        self,
        repository: MicWorkflowRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(self, command: ComputeMicRevision) -> MicRevisionResult:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        if command.algorithm_version != MIC_ENDPOINT_VERSION:
            raise ValueError(f"Unsupported MIC algorithm version: {command.algorithm_version}")
        snapshot = _mic_snapshot(self.repository, command.plate_id)
        wells = mic_wells_from_snapshot(snapshot)
        analysis = analyze_mic_endpoint(wells, command.threshold)
        input_sha256 = mic_input_sha256(wells, command.threshold)
        with self.repository.transaction():
            revision_id = persist_mic_analysis(
                self.repository,
                command.plate_id,
                actor_id,
                wells,
                well_ids_from_snapshot(snapshot),
                analysis,
                self.id_factory,
                parameters=command.parameters,
            )
            self.repository.append_provenance(
                {
                    "event_id": self.id_factory(),
                    "actor_id": actor_id,
                    "event_type": "mic_revision_computed",
                    "entity_type": "revision",
                    "entity_id": revision_id,
                    "details_json": {
                        "plate_id": command.plate_id,
                        "threshold": command.threshold,
                        "input_sha256": input_sha256,
                        "result_count": len(analysis.results),
                    },
                }
            )
        return MicRevisionResult(
            revision_id,
            len(analysis.well_calls),
            len(analysis.results),
            _analysis_issues(analysis),
            input_sha256,
        )


class UpdateMicMetadataService:
    def __init__(
        self,
        repository: MicWorkflowRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(self, command: UpdateMicMetadata) -> MicPlateView:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        snapshot = _mic_snapshot(self.repository, command.plate_id)
        experiment_changes = _present(
            {
                "name": command.experiment_name,
                "project": command.project,
                "notes": command.notes,
            }
        )
        plate_changes = _present(
            {
                "plate_name": command.plate_name,
                "instrument": command.instrument,
                "threshold": command.threshold,
                "lifecycle_status": command.lifecycle_status,
            }
        )
        if not experiment_changes and not plate_changes:
            raise ValueError("At least one MIC metadata field must be changed")
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
            revision_id: RevisionId | None = None
            if command.threshold is not None:
                changed_snapshot = _mic_snapshot(self.repository, command.plate_id)
                wells = mic_wells_from_snapshot(changed_snapshot)
                analysis = analyze_mic_endpoint(wells, command.threshold)
                revision_id = persist_mic_analysis(
                    self.repository,
                    command.plate_id,
                    actor_id,
                    wells,
                    well_ids_from_snapshot(changed_snapshot),
                    analysis,
                    self.id_factory,
                    parameters={"source": "metadata_threshold_update"},
                )
            self.repository.append_provenance(
                {
                    "event_id": self.id_factory(),
                    "actor_id": actor_id,
                    "event_type": "mic_metadata_updated",
                    "entity_type": "plate",
                    "entity_id": command.plate_id,
                    "details_json": {
                        "experiment_fields": sorted(experiment_changes),
                        "plate_fields": sorted(plate_changes),
                        "revision_id": revision_id,
                    },
                }
            )
        return _load_view(self.repository, command.plate_id)


class UpdateMicLayoutService:
    def __init__(
        self,
        repository: MicWorkflowRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(self, command: UpdateMicLayout) -> MicPlateView:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        snapshot = _mic_snapshot(self.repository, command.plate_id)
        changes = [_layout_values(change) for change in command.changes]
        if not changes:
            raise ValueError("At least one MIC well change is required")
        positions = [str(change["position"]) for change in changes]
        if len(positions) != len(set(positions)):
            raise ValueError("MIC layout changes repeat a well position")
        threshold = _number(snapshot.metadata["threshold"])
        with self.repository.transaction():
            self.repository.update_plate_metadata(command.plate_id, command.expected_updated_at, {})
            self.repository.update_well_layout(command.plate_id, changes)
            changed_snapshot = _mic_snapshot(self.repository, command.plate_id)
            wells = mic_wells_from_snapshot(changed_snapshot)
            analysis = analyze_mic_endpoint(wells, threshold)
            revision_id = persist_mic_analysis(
                self.repository,
                command.plate_id,
                actor_id,
                wells,
                well_ids_from_snapshot(changed_snapshot),
                analysis,
                self.id_factory,
                parameters={"source": "layout_update", "positions": positions},
            )
            self.repository.append_provenance(
                {
                    "event_id": self.id_factory(),
                    "actor_id": actor_id,
                    "event_type": "mic_layout_updated",
                    "entity_type": "plate",
                    "entity_id": command.plate_id,
                    "details_json": {"positions": positions, "revision_id": revision_id},
                }
            )
        return _load_view(self.repository, command.plate_id)


class SetMicReviewStateService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: SetMicReviewState) -> MicPlateView:
        return _set_state(
            self.repository,
            command.actor,
            command.plate_id,
            command.expected_updated_at,
            {"is_checked": command.checked},
            "mic_review_state_changed",
            {Role.EDITOR, Role.ADMIN},
        )


class SetMicLockStateService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: SetMicLockState) -> MicPlateView:
        return _set_state(
            self.repository,
            command.actor,
            command.plate_id,
            command.expected_updated_at,
            {"is_locked": command.locked},
            "mic_lock_state_changed",
            {Role.ADMIN},
        )


class SoftDeleteMicPlateService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: SoftDeleteMicPlate) -> MicPlateView:
        actor_id = require_role(self.repository, command.actor, {Role.ADMIN})
        snapshot = _mic_snapshot(self.repository, command.plate_id)
        if bool(snapshot.metadata["is_locked"]):
            raise PermissionError("Locked MIC plates cannot be deleted")
        if snapshot.metadata["deleted_at"] is not None:
            raise ValueError("MIC plate is already deleted")
        deleted_at = datetime.now(UTC).isoformat()
        with self.repository.transaction():
            self.repository.update_plate_metadata(
                command.plate_id,
                command.expected_updated_at,
                {"deleted_at": deleted_at, "deleted_by": actor_id},
            )
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "mic_plate_soft_deleted",
                    "entity_type": "plate",
                    "entity_id": command.plate_id,
                    "details_json": {"deleted_at": deleted_at},
                }
            )
        return _load_view(self.repository, command.plate_id)


class RestoreMicPlateService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: SoftDeleteMicPlate) -> MicPlateView:
        actor_id = require_role(self.repository, command.actor, {Role.ADMIN})
        snapshot = _mic_snapshot(self.repository, command.plate_id)
        if snapshot.metadata["deleted_at"] is None:
            raise ValueError("MIC plate is not deleted")
        with self.repository.transaction():
            self.repository.update_plate_metadata(
                command.plate_id,
                command.expected_updated_at,
                {"deleted_at": None, "deleted_by": None},
            )
            self.repository.append_provenance(
                {
                    "actor_id": actor_id,
                    "event_type": "mic_plate_restored",
                    "entity_type": "plate",
                    "entity_id": command.plate_id,
                }
            )
        return _load_view(self.repository, command.plate_id)


class SearchMicResultsService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, query: SearchMicResults) -> tuple[dict[str, object], ...]:
        require_role(self.repository, query.actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        if query.include_deleted and query.actor.role is not Role.ADMIN:
            raise PermissionError("Only admins may search deleted MIC plates")
        return self.repository.search_mic_results(
            {
                "strain": query.strain,
                "treatment": query.treatment,
                "medium": query.medium,
                "text": query.text,
                "include_deleted": query.include_deleted,
                "limit": query.limit,
                "offset": query.offset,
            }
        )


class SearchMicPlatesService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, query: SearchRuns) -> Sequence[RunSummary]:
        require_role(self.repository, query.actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        if query.include_deleted and query.actor.role is not Role.ADMIN:
            raise PermissionError("Only admins may search deleted MIC plates")
        return self.repository.search_runs(
            {
                "text": query.text,
                "assay_type": AssayType.MIC,
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


class LoadMicPlateService:
    def __init__(self, repository: MicWorkflowRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, plate_id: PlateId, revision_id: RevisionId | None = None
    ) -> MicPlateView:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        return _load_view(self.repository, plate_id, revision_id)

    def cache_token(self, actor: Actor, plate_id: PlateId) -> str:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        token = self.repository.plate_cache_token(plate_id)
        if token is None:
            raise LookupError(f"MIC plate not found: {plate_id}")
        return token


class ExportMicPlateService:
    def __init__(self, repository: MicWorkflowRepository, exporter: PortableRunExporter) -> None:
        self.repository = repository
        self.exporter = exporter

    def execute(self, command: ExportPortableRun) -> PortableArtifact:
        require_role(self.repository, command.actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        for plate_id in command.plate_ids:
            _mic_snapshot(self.repository, plate_id)
        filename, content = self.exporter.export(command.plate_ids, command.revision_ids)
        return PortableArtifact(filename, content)


def _set_state(
    repository: MicWorkflowRepository,
    actor: Actor,
    plate_id: PlateId,
    expected_updated_at: str,
    changes: dict[str, object],
    event_type: str,
    roles: set[Role],
) -> MicPlateView:
    actor_id = require_role(repository, actor, roles)
    _mic_snapshot(repository, plate_id)
    with repository.transaction():
        repository.update_plate_metadata(plate_id, expected_updated_at, changes)
        repository.append_provenance(
            {
                "actor_id": actor_id,
                "event_type": event_type,
                "entity_type": "plate",
                "entity_id": plate_id,
                "details_json": changes,
            }
        )
    return _load_view(repository, plate_id)


def _load_view(
    repository: MicWorkflowRepository,
    plate_id: PlateId,
    revision_id: RevisionId | None = None,
) -> MicPlateView:
    snapshot = _mic_snapshot(repository, plate_id)
    selected = revision_id or _current_mic_revision(snapshot)
    return MicPlateView(
        snapshot,
        repository.mic_well_calls(selected) if selected else (),
        repository.mic_results(selected) if selected else (),
        repository.provenance_for_plate(plate_id),
    )


def _mic_snapshot(repository: MicWorkflowRepository, plate_id: PlateId) -> PlateSnapshot:
    snapshot = repository.load_plate(plate_id)
    if snapshot is None:
        raise LookupError(f"MIC plate not found: {plate_id}")
    if str(snapshot.metadata["assay_type"]) != AssayType.MIC:
        raise ValueError(f"Plate is not a MIC plate: {plate_id}")
    return snapshot


def _current_mic_revision(snapshot: PlateSnapshot) -> RevisionId | None:
    for revision in reversed(snapshot.revisions):
        if revision["algorithm_name"] == "mic_endpoint" and bool(revision["is_current"]):
            return RevisionId(str(revision["revision_id"]))
    return None


def _layout_values(change: MicWellLayoutChange) -> dict[str, object]:
    values: dict[str, object] = {"position": change.position}
    optional_values: tuple[tuple[str, object | None], ...] = (
        ("display_name", change.display_name),
        ("is_blank", change.is_blank),
        ("strain", change.strain),
        ("treatment", change.treatment),
        ("concentration", change.concentration),
        ("concentration_unit", change.concentration_unit),
        ("medium", change.medium),
        ("replicate", change.replicate),
        ("notes", change.notes),
        ("custom_json", change.custom_labels),
    )
    for key, value in optional_values:
        if value is not None:
            # Empty text is an explicit clear; None means the caller omitted the field.
            values[key] = None if isinstance(value, str) and not value.strip() else value
    return values


def _present(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expected a numeric MIC threshold")
    return float(value)


def _analysis_issues(analysis: object) -> tuple[DomainIssue, ...]:
    from plate_reader.domain.mic import MicAnalysisResult

    if not isinstance(analysis, MicAnalysisResult):
        raise TypeError("Expected MicAnalysisResult")
    return (
        *analysis.issues,
        *(issue for result in analysis.results for issue in result.issues),
    )
