"""Atomic, idempotent MIC long-CSV import and initial analysis."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Protocol

from plate_reader.application.contracts import (
    AssayType,
    ExperimentId,
    ImportMicPlate,
    MicExperimentMetadata,
    MicWellLayoutChange,
    PlateId,
    RevisionId,
    Role,
)
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_import import (
    SourceHashMismatchError,
    UnsupportedParserVersionError,
)
from plate_reader.application.services.mic_common import persist_mic_analysis
from plate_reader.application.services.source_limits import (
    MAX_MIC_SOURCE_BYTES,
    source_bytes_within_limit,
)
from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import PLATE_96, WellPosition
from plate_reader.domain.mic import (
    MIC_PLATE_PARSER_VERSION,
    MicWell,
    analyze_mic_endpoint,
    parse_mic_plate_csv,
)


class MicImportRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def plate_for_source(self, idempotency_key: str) -> PlateId | None: ...

    def create_experiment(self, values: dict[str, object]) -> ExperimentId: ...

    def create_plate(self, values: dict[str, object]) -> PlateId: ...

    def insert_wells(self, plate_id: PlateId, rows: Sequence[dict[str, object]]) -> None: ...

    def insert_conditions(self, rows: Sequence[dict[str, object]]) -> None: ...

    def insert_raw_observations(
        self, plate_id: PlateId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def add_analysis_revision(self, values: dict[str, object]) -> RevisionId: ...

    def insert_mic_well_calls(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def insert_mic_results(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def record_import_source(self, values: Mapping[str, object]) -> str: ...

    def append_provenance(self, values: Mapping[str, object]) -> str: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...

    def mic_results(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True, slots=True)
class MicPreview:
    source_sha256: str
    well_count: int
    blank_count: int
    group_count: int
    result_count: int
    background_value: float
    issues: tuple[DomainIssue, ...]


@dataclass(frozen=True, slots=True)
class MicImportResult:
    experiment_id: ExperimentId
    plate_id: PlateId
    revision_id: RevisionId
    created: bool
    idempotency_key: str
    well_count: int
    result_count: int
    issues: tuple[DomainIssue, ...]


class PreviewMicPlateService:
    def execute(self, csv_text: str, threshold: float) -> MicPreview:
        source_bytes = source_bytes_within_limit(
            csv_text, max_bytes=MAX_MIC_SOURCE_BYTES, kind="MIC CSV"
        )
        wells = parse_mic_plate_csv(csv_text)
        _require_complete_plate(wells)
        analysis = analyze_mic_endpoint(wells, threshold)
        return MicPreview(
            source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            well_count=len(wells),
            blank_count=sum(well.is_blank for well in wells),
            group_count=len(analysis.results),
            result_count=len(analysis.results),
            background_value=analysis.background_value,
            issues=_all_issues(analysis),
        )


class ImportMicPlateService:
    def __init__(
        self,
        repository: MicImportRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(
        self,
        command: ImportMicPlate,
        csv_text: str,
        *,
        metadata: MicExperimentMetadata | None = None,
        layout_changes: Sequence[MicWellLayoutChange] = (),
    ) -> MicImportResult:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        if command.parser_version != MIC_PLATE_PARSER_VERSION:
            raise UnsupportedParserVersionError(
                f"Unsupported MIC parser version: {command.parser_version}"
            )
        source_bytes = source_bytes_within_limit(
            csv_text, max_bytes=MAX_MIC_SOURCE_BYTES, kind="MIC CSV"
        )
        actual_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_hash != command.source_sha256.casefold():
            raise SourceHashMismatchError(
                f"MIC source hash mismatch: expected {command.source_sha256}, got {actual_hash}"
            )
        idempotency_key = command.idempotency_key or (
            f"mic_plate:{actual_hash}:{command.parser_version}"
        )
        if existing := self.repository.plate_for_source(idempotency_key):
            snapshot = _mic_snapshot(self.repository, existing)
            revision = _current_mic_revision(snapshot)
            if revision is None:
                raise RuntimeError("Imported MIC source has no analysis revision")
            return MicImportResult(
                ExperimentId(str(snapshot.metadata["experiment_id"])),
                existing,
                revision,
                False,
                idempotency_key,
                len(snapshot.wells),
                len(self.repository.mic_results(revision)),
                (),
            )
        wells = _apply_layout_changes(parse_mic_plate_csv(csv_text), layout_changes)
        _require_complete_plate(wells)
        analysis = analyze_mic_endpoint(wells, command.threshold)
        experiment_id = ExperimentId(self.id_factory())
        plate_id = PlateId(self.id_factory())
        well_ids = {well.position: self.id_factory() for well in wells}
        details = metadata or MicExperimentMetadata()
        with self.repository.transaction():
            self.repository.create_experiment(
                {
                    "experiment_id": experiment_id,
                    "name": command.experiment_name,
                    "experiment_date": command.experiment_date.isoformat(),
                    "operator_name": details.operator_name,
                    "reader": details.reader,
                    "incubation_time_hours": details.incubation_time_hours,
                    "inoculum_od": details.inoculum_od,
                    "growth_phase": details.growth_phase,
                    "harvest_od": details.harvest_od,
                    "doubling_time_minutes": details.doubling_time_minutes,
                    "notes": details.notes,
                    "custom_json": details.custom_json,
                    "created_by": actor_id,
                }
            )
            self.repository.create_plate(
                {
                    "plate_id": plate_id,
                    "experiment_id": experiment_id,
                    "assay_type": AssayType.MIC,
                    "plate_name": command.plate_name,
                    "plate_format": 96,
                    "channel": "od",
                    "threshold": command.threshold,
                    "threshold_method": "fixed",
                    "background_method": "average_blanks",
                    "created_by": actor_id,
                }
            )
            self.repository.insert_wells(
                plate_id,
                [
                    {
                        "well_id": well_ids[well.position],
                        "position": well.position.label,
                        "row_index": well.position.row_index,
                        "column_index": well.position.column_index,
                        "is_blank": well.is_blank,
                        "display_name": _display_name(layout_changes, well.position),
                        "notes": well.notes,
                        "custom_json": dict(well.custom_labels),
                    }
                    for well in wells
                ],
            )
            self.repository.insert_conditions(
                [
                    {
                        "well_id": well_ids[well.position],
                        "strain": well.strain,
                        "treatment": well.treatment,
                        "concentration": well.concentration,
                        "concentration_unit": well.concentration_unit,
                        "medium": well.medium,
                        "replicate": well.replicate,
                    }
                    for well in wells
                ]
            )
            self.repository.insert_raw_observations(
                plate_id,
                [
                    {
                        "well_id": well_ids[well.position],
                        "channel": "od",
                        "value_raw": well.value_raw,
                    }
                    for well in wells
                ],
            )
            revision_id = persist_mic_analysis(
                self.repository,
                plate_id,
                actor_id,
                wells,
                well_ids,
                analysis,
                self.id_factory,
                parameters={"source": "mic_plate_import"},
            )
            self.repository.record_import_source(
                {
                    "source_id": self.id_factory(),
                    "plate_id": plate_id,
                    "source_kind": "mic_plate",
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
                    "event_type": "mic_plate_imported",
                    "entity_type": "plate",
                    "entity_id": plate_id,
                    "details_json": {
                        "source_sha256": actual_hash,
                        "revision_id": revision_id,
                        "well_count": len(wells),
                        "result_count": len(analysis.results),
                        "issue_codes": [issue.code for issue in _all_issues(analysis)],
                    },
                }
            )
        return MicImportResult(
            experiment_id,
            plate_id,
            revision_id,
            True,
            idempotency_key,
            len(wells),
            len(analysis.results),
            _all_issues(analysis),
        )


def _require_complete_plate(wells: Sequence[MicWell]) -> None:
    positions = {well.position for well in wells}
    expected = set(PLATE_96.positions())
    if positions != expected:
        missing = sorted(position.label for position in expected - positions)
        raise DomainValidationError(
            DomainIssue.error(
                IssueCode.MISSING_WELLS,
                "MIC import requires exactly one value for every 96-well position.",
                missing=missing,
            )
        )


def _apply_layout_changes(
    wells: Sequence[MicWell], changes: Sequence[MicWellLayoutChange]
) -> tuple[MicWell, ...]:
    change_by_position: dict[WellPosition, MicWellLayoutChange] = {}
    for change in changes:
        position = WellPosition.parse(change.position, PLATE_96)
        if position in change_by_position:
            raise ValueError(f"MIC layout changes repeat {position.label}")
        change_by_position[position] = change
    return tuple(
        replace(
            well,
            value_raw=(
                change.value_raw if change and change.value_raw is not None else well.value_raw
            ),
            is_blank=change.is_blank if change and change.is_blank is not None else well.is_blank,
            strain=(
                change.strain.strip() or None
                if change and change.strain is not None
                else well.strain
            ),
            treatment=(
                change.treatment.strip() or None
                if change and change.treatment is not None
                else well.treatment
            ),
            concentration=(
                change.concentration
                if change and change.concentration is not None
                else well.concentration
            ),
            concentration_unit=(
                change.concentration_unit
                if change and change.concentration_unit is not None
                else well.concentration_unit
            ),
            medium=(
                change.medium.strip() or None
                if change and change.medium is not None
                else well.medium
            ),
            replicate=(
                change.replicate if change and change.replicate is not None else well.replicate
            ),
            notes=(
                change.notes.strip() or None if change and change.notes is not None else well.notes
            ),
            custom_labels=(
                tuple(sorted(change.custom_labels.items()))
                if change and change.custom_labels is not None
                else well.custom_labels
            ),
        )
        for well in wells
        for change in (change_by_position.get(well.position),)
    )


def _display_name(changes: Sequence[MicWellLayoutChange], position: WellPosition) -> str | None:
    return next(
        (
            change.display_name
            for change in changes
            if WellPosition.parse(change.position, PLATE_96) == position
        ),
        None,
    )


def _mic_snapshot(repository: MicImportRepository, plate_id: PlateId) -> PlateSnapshot:
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


def _all_issues(analysis: object) -> tuple[DomainIssue, ...]:
    from plate_reader.domain.mic import MicAnalysisResult

    if not isinstance(analysis, MicAnalysisResult):
        raise TypeError("Expected MicAnalysisResult")
    return (
        *analysis.issues,
        *(issue for result in analysis.results for issue in result.issues),
    )
