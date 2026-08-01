"""Read-only migration of legacy MIC SQLite databases into schema v1."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    ExperimentId,
    PlateId,
    RevisionId,
    Role,
)
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.mic_common import persist_mic_analysis
from plate_reader.domain.common.plate import PLATE_96, WellPosition
from plate_reader.domain.mic import MicAnalysisResult, MicWell, analyze_mic_endpoint
from plate_reader.infrastructure.database.repository import SqlPlateReaderRepository

LEGACY_MIC_IMPORT_VERSION = "legacy-mic-sqlite/1.0.0"
REQUIRED_TABLES = {"experiments", "plates", "wells", "mic_results"}


class LegacyMicValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyMicPlatePreview:
    plate_id: str
    experiment_id: str
    plate_name: str
    well_count: int
    legacy_result_count: int
    calculated_result_count: int
    raw_sha256: str
    is_deleted: bool
    is_locked: bool
    is_checked: bool
    missing_target_fields: tuple[str, ...]
    derived_differences: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyMicFilePreview:
    path: Path
    file_sha256: str
    byte_size: int
    detected_version: str
    plates: tuple[LegacyMicPlatePreview, ...]


@dataclass(frozen=True, slots=True)
class LegacyMicPlateImport:
    legacy_plate_id: str
    status: str
    plate_id: str | None
    revision_id: str | None
    source_raw_sha256: str
    imported_raw_sha256: str | None
    counts: dict[str, int]
    derived_differences: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyMicImportReport:
    source: LegacyMicFilePreview
    dry_run: bool
    source_unchanged: bool
    plates: tuple[LegacyMicPlateImport, ...]


def preview_legacy_mic_file(path: Path) -> LegacyMicFilePreview:
    source_hash = _file_sha256(path)
    connection = _open_read_only(path)
    try:
        version = _detect_version(connection)
        plate_rows = _dict_rows(connection, "SELECT * FROM plates ORDER BY plate_id")
        previews = tuple(_preview_plate(connection, row) for row in plate_rows)
        if not previews:
            raise LegacyMicValidationError("Legacy MIC database contains no plates")
        return LegacyMicFilePreview(
            path,
            source_hash,
            path.stat().st_size,
            version,
            previews,
        )
    finally:
        connection.close()


def import_legacy_mic_file(
    path: Path,
    repository: SqlPlateReaderRepository,
    actor: Actor,
    *,
    dry_run: bool = True,
    allow_derived_differences: bool = False,
    id_factory: Callable[[], str] | None = None,
    now_factory: Callable[[], str] | None = None,
) -> LegacyMicImportReport:
    preview = preview_legacy_mic_file(path)
    if dry_run:
        return LegacyMicImportReport(
            preview,
            True,
            _file_sha256(path) == preview.file_sha256,
            tuple(_dry_run_plate(repository, preview, plate) for plate in preview.plates),
        )
    actor_id = require_role(repository, actor, {Role.EDITOR, Role.ADMIN})
    errors = [error for plate in preview.plates for error in plate.errors]
    differences = [
        difference for plate in preview.plates for difference in plate.derived_differences
    ]
    if errors:
        raise LegacyMicValidationError("; ".join(errors))
    if differences and not allow_derived_differences:
        raise LegacyMicValidationError(
            "Legacy derived MIC values differ from mic-endpoint/1.0.0; review the dry-run "
            "report or explicitly allow documented differences"
        )
    make_id = id_factory or (lambda: str(uuid.uuid4()))
    now = now_factory or (lambda: datetime.now(UTC).isoformat())
    source = _open_read_only(path)
    reports: list[LegacyMicPlateImport] = []
    try:
        with repository.transaction():
            for plate_preview in preview.plates:
                existing = repository.plate_for_source(
                    _idempotency_key(preview.file_sha256, plate_preview.plate_id)
                )
                collision = repository.plate_for_legacy_run_id(plate_preview.plate_id)
                if existing is not None or collision is not None:
                    reports.append(_dry_run_plate(repository, preview, plate_preview))
                    continue
                reports.append(
                    _import_plate(
                        source,
                        repository,
                        actor_id,
                        preview,
                        plate_preview,
                        make_id,
                        now,
                    )
                )
            if _file_sha256(path) != preview.file_sha256:
                raise LegacyMicValidationError("Legacy MIC source changed during import")
    finally:
        source.close()
    return LegacyMicImportReport(preview, False, True, tuple(reports))


def _preview_plate(
    connection: sqlite3.Connection, plate_row: dict[str, object]
) -> LegacyMicPlatePreview:
    plate_id = _text(plate_row.get("plate_id"))
    experiment_id = _text(plate_row.get("experiment_id"))
    if not plate_id or not experiment_id:
        raise LegacyMicValidationError("Legacy MIC plate lacks plate_id or experiment_id")
    experiment = _one(connection, "experiments", "experiment_id", experiment_id)
    wells = _rows_for(connection, "wells", "plate_id", plate_id, "row, column, well_position")
    legacy_results = _rows_for(
        connection, "mic_results", "plate_id", plate_id, "strain, antibiotic, media, replicate"
    )
    warnings = ["Legacy MIC experiments have no experiment-name field; plate_name will be used."]
    errors: list[str] = []
    if not _text(experiment.get("date")):
        errors.append(f"Legacy MIC plate {plate_id} has no experiment date")
    mic_wells: list[MicWell] = []
    positions: list[WellPosition] = []
    for row in wells:
        try:
            position = WellPosition.parse(_text(row.get("well_position")), PLATE_96)
            positions.append(position)
            if row.get("row") != position.row_index or row.get("column") != position.column_index:
                warnings.append(f"{position.label}: stored coordinates disagree with position")
            mic_wells.append(_mic_well(row, position))
        except ValueError as error:
            errors.append(f"{plate_id}: {error}")
    if len(positions) != len(set(positions)):
        errors.append(f"Legacy MIC plate {plate_id} repeats well positions")
    if set(positions) != set(PLATE_96.positions()):
        errors.append(f"Legacy MIC plate {plate_id} is not a complete 96-well plate")
    if plate_row.get("plate_format") in (None, ""):
        warnings.append(
            "Legacy plate_format is absent; 96 is inferred from the validated complete layout."
        )
    if not _optional_text(plate_row.get("threshold_method")):
        warnings.append("Legacy threshold_method is absent; fixed is used and reported.")
    if not _optional_text(plate_row.get("background_method")):
        warnings.append("Legacy background_method is absent; average_blanks is used and reported.")
    analysis: MicAnalysisResult | None = None
    differences: tuple[str, ...] = ()
    if not errors:
        analysis = analyze_mic_endpoint(mic_wells, _number(plate_row.get("threshold"), "threshold"))
        differences = _derived_differences(wells, legacy_results, analysis)
        if differences:
            warnings.append(f"Found {len(differences)} derived MIC difference(s)")
    is_deleted = _bool(plate_row.get("is_deleted"))
    if is_deleted:
        warnings.append(
            "Legacy deletion has no actor/time; migration actor/time will be recorded "
            "as a surrogate."
        )
    missing = tuple(
        field
        for field in (
            "experiment_name",
            "project",
            "instrument",
            "plate_format",
            "threshold_method",
            "background_method",
            "is_deleted",
            "is_locked",
            "is_checked",
            "deletion_actor",
            "deletion_time",
        )
        if field not in plate_row or plate_row.get(field) in (None, "")
    )
    return LegacyMicPlatePreview(
        plate_id=plate_id,
        experiment_id=experiment_id,
        plate_name=_text(plate_row.get("plate_name")) or plate_id,
        well_count=len(wells),
        legacy_result_count=len(legacy_results),
        calculated_result_count=0 if analysis is None else len(analysis.results),
        raw_sha256=_legacy_raw_hash(wells),
        is_deleted=is_deleted,
        is_locked=_bool(plate_row.get("is_locked")),
        is_checked=_bool(plate_row.get("is_checked")),
        missing_target_fields=missing,
        derived_differences=differences,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _import_plate(
    source: sqlite3.Connection,
    repository: SqlPlateReaderRepository,
    actor_id: str,
    file_preview: LegacyMicFilePreview,
    plate_preview: LegacyMicPlatePreview,
    id_factory: Callable[[], str],
    now_factory: Callable[[], str],
) -> LegacyMicPlateImport:
    legacy_plate = _one(source, "plates", "plate_id", plate_preview.plate_id)
    legacy_experiment = _one(source, "experiments", "experiment_id", plate_preview.experiment_id)
    legacy_wells = _rows_for(
        source, "wells", "plate_id", plate_preview.plate_id, "row, column, well_position"
    )
    legacy_results = _rows_for(
        source,
        "mic_results",
        "plate_id",
        plate_preview.plate_id,
        "strain, antibiotic, media, replicate",
    )
    experiment_id = ExperimentId(id_factory())
    plate_id = PlateId(id_factory())
    wells = tuple(
        _mic_well(row, WellPosition.parse(_text(row.get("well_position")), PLATE_96))
        for row in legacy_wells
    )
    well_ids = {well.position: id_factory() for well in wells}
    threshold = _number(legacy_plate.get("threshold"), "threshold")
    analysis = analyze_mic_endpoint(wells, threshold)
    created_at = _text(legacy_plate.get("created_at")) or now_factory()
    repository.create_experiment(
        {
            "experiment_id": experiment_id,
            "name": plate_preview.plate_name,
            "experiment_date": _required_text(legacy_experiment.get("date"), "date"),
            "operator_name": _optional_text(legacy_experiment.get("person")),
            "reader": _optional_text(legacy_experiment.get("reader")),
            "incubation_time_hours": legacy_experiment.get("incubation_time"),
            "inoculum_od": legacy_experiment.get("inoculum_od"),
            "growth_phase": _optional_text(legacy_experiment.get("growth_phase")),
            "harvest_od": legacy_experiment.get("harvest_od"),
            "doubling_time_minutes": legacy_experiment.get("doubling_time"),
            "notes": _optional_text(legacy_experiment.get("notes")),
            "custom_json": {
                "legacy_experiment": legacy_experiment,
                "legacy_extra_metadata": _json_object(legacy_experiment.get("extra_metadata_json")),
            },
            "created_by": actor_id,
            "created_at": created_at,
        }
    )
    deletion_time = now_factory() if plate_preview.is_deleted else None
    repository.create_plate(
        {
            "plate_id": plate_id,
            "experiment_id": experiment_id,
            "assay_type": AssayType.MIC,
            "plate_name": plate_preview.plate_name,
            "plate_format": _plate_format(legacy_plate.get("plate_format"), wells),
            "channel": "od",
            "threshold": threshold,
            "threshold_method": _optional_text(legacy_plate.get("threshold_method")) or "fixed",
            "background_method": _optional_text(legacy_plate.get("background_method"))
            or "average_blanks",
            "is_locked": plate_preview.is_locked,
            "is_checked": plate_preview.is_checked,
            "legacy_run_id": plate_preview.plate_id,
            "custom_json": {"legacy_plate": legacy_plate},
            "created_by": actor_id,
            "created_at": created_at,
            "deleted_at": deletion_time,
            "deleted_by": actor_id if deletion_time else None,
        }
    )
    repository.insert_wells(
        plate_id,
        [
            {
                "well_id": well_ids[well.position],
                "position": well.position.label,
                "row_index": well.position.row_index,
                "column_index": well.position.column_index,
                "is_blank": well.is_blank,
                "notes": well.notes,
                "custom_json": dict(well.custom_labels),
            }
            for well in wells
        ],
    )
    repository.insert_conditions(
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
    repository.insert_raw_observations(
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
        repository,
        plate_id,
        actor_id,
        wells,
        well_ids,
        analysis,
        id_factory,
        parameters={"source": "legacy_mic", "legacy_plate_id": plate_preview.plate_id},
    )
    repository.record_import_source(
        {
            "source_id": id_factory(),
            "plate_id": plate_id,
            "source_kind": "legacy_mic",
            "original_filename": file_preview.path.name,
            "content_sha256": file_preview.file_sha256,
            "byte_size": file_preview.byte_size,
            "parser_version": LEGACY_MIC_IMPORT_VERSION,
            "idempotency_key": _idempotency_key(file_preview.file_sha256, plate_preview.plate_id),
            "status": "imported",
            "imported_by": actor_id,
            "custom_json": {
                "detected_version": file_preview.detected_version,
                "legacy_plate_id": plate_preview.plate_id,
            },
        }
    )
    repository.append_provenance(
        {
            "event_id": id_factory(),
            "actor_id": actor_id,
            "event_type": "legacy_mic_imported",
            "entity_type": "plate",
            "entity_id": plate_id,
            "details_json": {
                "legacy_plate_id": plate_preview.plate_id,
                "source_file_sha256": file_preview.file_sha256,
                "source_raw_sha256": plate_preview.raw_sha256,
                "revision_id": revision_id,
                "legacy_results": legacy_results,
                "derived_differences": plate_preview.derived_differences,
                "missing_target_fields": plate_preview.missing_target_fields,
                "deletion_surrogate": (
                    {"actor_id": actor_id, "occurred_at": deletion_time} if deletion_time else None
                ),
            },
        }
    )
    imported_hash = _destination_raw_hash(repository, plate_id)
    if imported_hash != plate_preview.raw_sha256:
        raise LegacyMicValidationError(
            f"Raw verification failed for legacy MIC plate {plate_preview.plate_id}"
        )
    return LegacyMicPlateImport(
        plate_preview.plate_id,
        "imported",
        str(plate_id),
        str(revision_id),
        plate_preview.raw_sha256,
        imported_hash,
        _destination_counts(repository, plate_id, revision_id),
        plate_preview.derived_differences,
        plate_preview.warnings,
    )


def _dry_run_plate(
    repository: SqlPlateReaderRepository,
    file_preview: LegacyMicFilePreview,
    plate: LegacyMicPlatePreview,
) -> LegacyMicPlateImport:
    existing = repository.plate_for_source(
        _idempotency_key(file_preview.file_sha256, plate.plate_id)
    )
    collision = repository.plate_for_legacy_run_id(plate.plate_id)
    target = existing or collision
    warnings = list(plate.warnings)
    if existing is not None:
        status = "skipped_duplicate_source"
        warnings.append("This exact legacy MIC source and plate were already imported.")
    elif collision is not None:
        status = "skipped_duplicate_plate_id"
        warnings.append("The legacy MIC plate ID already exists; default policy skips it.")
    else:
        status = "blocked" if plate.errors else "ready"
    if target is None:
        counts = {
            "wells": plate.well_count,
            "readings": plate.well_count,
            "calls": plate.well_count,
            "results": plate.calculated_result_count,
        }
        imported_hash = None
    else:
        revision = _current_revision_id(repository, target)
        counts = _destination_counts(repository, target, revision)
        imported_hash = _destination_raw_hash(repository, target)
        if imported_hash != plate.raw_sha256:
            warnings.append("Existing plate has a different immutable raw MIC hash.")
    selected_revision = None if target is None else _current_revision_id(repository, target)
    return LegacyMicPlateImport(
        plate.plate_id,
        status,
        None if target is None else str(target),
        None if selected_revision is None else str(selected_revision),
        plate.raw_sha256,
        imported_hash,
        counts,
        plate.derived_differences,
        tuple(warnings),
    )


def _derived_differences(
    legacy_wells: Sequence[Mapping[str, object]],
    legacy_results: Sequence[Mapping[str, object]],
    analysis: MicAnalysisResult,
) -> tuple[str, ...]:
    differences: list[str] = []
    calls = {call.position.label: call for call in analysis.well_calls}
    for row in legacy_wells:
        position = _text(row.get("well_position"))
        call = calls[position]
        if not _close_optional(row.get("od_bg_subtracted"), call.value_background_subtracted):
            differences.append(f"{position}: background-subtracted OD differs")
        if row.get("growth_call") is not None and _bool(row.get("growth_call")) != call.growth_call:
            differences.append(f"{position}: growth call differs")
    legacy_by_group = {_legacy_result_key(row): row for row in legacy_results}
    calculated_by_group = {
        (result.strain, result.treatment, result.medium, result.replicate): result
        for result in analysis.results
    }
    for key in sorted(legacy_by_group.keys() | calculated_by_group.keys()):
        legacy = legacy_by_group.get(key)
        calculated = calculated_by_group.get(key)
        label = "/".join(str(value) for value in key)
        if legacy is None or calculated is None:
            differences.append(f"{label}: result group missing on one side")
            continue
        checks = {
            "mic_value": _close_optional(legacy.get("mic_value"), calculated.mic_value),
            "mic_operator": _text(legacy.get("mic_operator")) == calculated.mic_operator,
            "mic_unit": _text(legacy.get("mic_unit")) == calculated.mic_unit,
            "threshold": _close_optional(legacy.get("threshold_used"), calculated.threshold_used),
            "lowest": _close_optional(
                legacy.get("lowest_tested_conc"), calculated.lowest_tested_concentration
            ),
            "highest": _close_optional(
                legacy.get("highest_tested_conc"), calculated.highest_tested_concentration
            ),
            "point_count": legacy.get("num_points") == calculated.point_count,
            "concentrations": _json_list(legacy.get("concentration_values_json"))
            == list(calculated.concentrations),
            "warning": _optional_text(legacy.get("warning"))
            == ("; ".join(issue.message for issue in calculated.issues) or None),
        }
        differences.extend(
            f"{label}: {field} differs" for field, matches in checks.items() if not matches
        )
    return tuple(differences)


def _mic_well(row: Mapping[str, object], position: WellPosition) -> MicWell:
    return MicWell(
        position=position,
        value_raw=_number(row.get("od_raw"), "od_raw"),
        is_blank=_bool(row.get("is_blank")),
        strain=_optional_text(row.get("strain")),
        treatment=_optional_text(row.get("antibiotic")),
        concentration=_optional_number(row.get("concentration"), "concentration"),
        concentration_unit=_optional_text(row.get("concentration_unit")) or "ug/mL",
        medium=_optional_text(row.get("media")),
        replicate=_positive_int(row.get("replicate"), "replicate"),
        notes=_optional_text(row.get("notes")),
        custom_labels=tuple(sorted(_json_object(row.get("extra_labels_json")).items())),
    )


def _legacy_raw_hash(rows: Sequence[Mapping[str, object]]) -> str:
    payload = sorted(
        (_text(row.get("well_position")).upper(), "od", row.get("od_raw")) for row in rows
    )
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _destination_raw_hash(repository: SqlPlateReaderRepository, plate_id: PlateId) -> str:
    rows = repository.connection.execute(
        "SELECT w.position, mr.channel, mr.value_raw FROM mic_readings mr "
        "JOIN wells w ON w.well_id = mr.well_id WHERE mr.plate_id = ? "
        "ORDER BY w.position, mr.channel",
        (plate_id,),
    ).fetchall()
    payload = sorted((str(row[0]), str(row[1]), row[2]) for row in rows)
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _destination_counts(
    repository: SqlPlateReaderRepository, plate_id: PlateId, revision_id: RevisionId | None
) -> dict[str, int]:
    return {
        "wells": _count(repository, "SELECT count(*) FROM wells WHERE plate_id = ?", plate_id),
        "readings": _count(
            repository, "SELECT count(*) FROM mic_readings WHERE plate_id = ?", plate_id
        ),
        "calls": (
            0
            if revision_id is None
            else _count(
                repository,
                "SELECT count(*) FROM mic_well_calls WHERE revision_id = ?",
                revision_id,
            )
        ),
        "results": (
            0
            if revision_id is None
            else _count(
                repository,
                "SELECT count(*) FROM mic_results WHERE revision_id = ?",
                revision_id,
            )
        ),
    }


def _current_revision_id(
    repository: SqlPlateReaderRepository, plate_id: PlateId
) -> RevisionId | None:
    row = repository.connection.execute(
        "SELECT revision_id FROM analysis_revisions WHERE plate_id = ? "
        "AND algorithm_name = 'mic_endpoint' AND is_current = 1",
        (plate_id,),
    ).fetchone()
    return None if row is None else RevisionId(str(row[0]))


def _count(repository: SqlPlateReaderRepository, sql: str, identifier: PlateId | RevisionId) -> int:
    row = repository.connection.execute(sql, (identifier,)).fetchone()
    if row is None:
        raise LegacyMicValidationError("Count query returned no result")
    return int(str(row[0]))


def _detect_version(connection: sqlite3.Connection) -> str:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if not tables >= REQUIRED_TABLES:
        raise LegacyMicValidationError(
            f"Unsupported legacy MIC schema; missing tables: {sorted(REQUIRED_TABLES - tables)}"
        )
    required_columns = {
        "experiments": {"experiment_id", "date"},
        "plates": {"plate_id", "experiment_id", "plate_name", "threshold"},
        "wells": {"well_id", "plate_id", "well_position", "od_raw"},
        "mic_results": {"plate_id", "strain", "antibiotic", "mic_value", "mic_operator"},
    }
    for table, required in required_columns.items():
        missing = required - _columns(connection, table)
        if missing:
            raise LegacyMicValidationError(
                f"Unsupported legacy MIC {table} columns; missing: {sorted(missing)}"
            )
    plate_columns = _columns(connection, "plates")
    if {"is_deleted", "is_locked", "is_checked"} <= plate_columns:
        return "mic-sqlite-v1-current"
    return "mic-sqlite-v1-early"


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _one(connection: sqlite3.Connection, table: str, key: str, value: str) -> dict[str, object]:
    rows = _dict_rows(connection, f"SELECT * FROM {table} WHERE {key} = ?", (value,))
    if len(rows) != 1:
        raise LegacyMicValidationError(f"Expected one {table} row for {value}")
    return rows[0]


def _rows_for(
    connection: sqlite3.Connection,
    table: str,
    key: str,
    value: str,
    order_by: str,
) -> list[dict[str, object]]:
    return _dict_rows(
        connection, f"SELECT * FROM {table} WHERE {key} = ? ORDER BY {order_by}", (value,)
    )


def _dict_rows(
    connection: sqlite3.Connection, sql: str, parameters: Sequence[object] = ()
) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _legacy_result_key(row: Mapping[str, object]) -> tuple[str, str, str, int]:
    return (
        _optional_text(row.get("strain")) or "Unknown",
        _optional_text(row.get("antibiotic")) or "Unknown",
        _optional_text(row.get("media")) or "Unknown",
        _positive_int(row.get("replicate"), "replicate"),
    )


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LegacyMicValidationError(f"Legacy MIC {field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise LegacyMicValidationError(f"Legacy MIC {field} must be finite")
    return result


def _optional_number(value: object, field: str) -> float | None:
    return None if value in (None, "") else _number(value, field)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LegacyMicValidationError(f"Legacy MIC {field} must be a positive integer")
    return value


def _plate_format(value: object, wells: Sequence[MicWell]) -> int:
    if value in (None, "") and {well.position for well in wells} == set(PLATE_96.positions()):
        return 96
    return _positive_int(value, "plate_format")


def _bool(value: object) -> bool:
    if value in (None, 0, False, "", "0"):
        return False
    if value in (1, True, "1"):
        return True
    raise LegacyMicValidationError(f"Legacy MIC boolean is invalid: {value}")


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _optional_text(value: object) -> str | None:
    normalized = _text(value)
    return normalized or None


def _required_text(value: object, field: str) -> str:
    normalized = _text(value)
    if not normalized:
        raise LegacyMicValidationError(f"Legacy MIC {field} is required")
    return normalized


def _json_object(value: object) -> dict[str, str]:
    if value in (None, "", {}):
        return {}
    if not isinstance(value, str):
        raise LegacyMicValidationError("Legacy MIC JSON must be text")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise LegacyMicValidationError("Legacy MIC JSON is invalid") from error
    if not isinstance(parsed, dict):
        raise LegacyMicValidationError("Legacy MIC JSON must be an object")
    return {str(key): str(item) for key, item in parsed.items()}


def _json_list(value: object) -> list[object]:
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _close_optional(value: object, expected: float) -> bool:
    if value is None:
        return False
    try:
        actual = float(str(value))
    except ValueError:
        return False
    return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)


def _idempotency_key(file_sha256: str, plate_id: str) -> str:
    return f"legacy_mic:{file_sha256}:{plate_id}:{LEGACY_MIC_IMPORT_VERSION}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
