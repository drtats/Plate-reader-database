"""Read-only, version-detecting importer for growth v4 SQLite databases."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
from plate_reader.domain.common.plate import PLATE_96, WellPosition
from plate_reader.infrastructure.database.repository import SqlPlateReaderRepository

LEGACY_GROWTH_IMPORT_VERSION = "legacy-growth-sqlite/1.0.0"
REQUIRED_TABLES = {"plate_meta", "well_meta", "measurements"}
KNOWN_TARGET_GAPS = (
    "project",
    "instrument",
    "temperature",
    "temperature_unit",
    "manual_subtraction",
)


class LegacyGrowthValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyGrowthRunPreview:
    run_id: str
    experiment_name: str
    well_count: int
    measurement_count: int
    background_count: int
    raw_sha256: str
    missing_target_fields: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyGrowthFilePreview:
    path: Path
    file_sha256: str
    byte_size: int
    detected_version: str
    runs: tuple[LegacyGrowthRunPreview, ...]


@dataclass(frozen=True, slots=True)
class LegacyGrowthRunImport:
    run_id: str
    status: str
    plate_id: str | None
    source_raw_sha256: str
    imported_raw_sha256: str | None
    counts: dict[str, int]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyGrowthImportReport:
    source: LegacyGrowthFilePreview
    dry_run: bool
    source_unchanged: bool
    runs: tuple[LegacyGrowthRunImport, ...]


def preview_legacy_growth_file(path: Path) -> LegacyGrowthFilePreview:
    source_hash = _file_sha256(path)
    connection = _open_read_only(path)
    try:
        detected_version = _detect_version(connection)
        plate_rows = _dict_rows(connection, "SELECT * FROM plate_meta ORDER BY run_id")
        previews = tuple(_preview_run(connection, row) for row in plate_rows)
        if not previews:
            raise LegacyGrowthValidationError("Legacy database contains no runs")
        return LegacyGrowthFilePreview(
            path=path,
            file_sha256=source_hash,
            byte_size=path.stat().st_size,
            detected_version=detected_version,
            runs=previews,
        )
    finally:
        connection.close()


def import_legacy_growth_file(
    path: Path,
    repository: SqlPlateReaderRepository,
    actor: Actor,
    *,
    dry_run: bool = True,
    id_factory: Callable[[], str] | None = None,
) -> LegacyGrowthImportReport:
    preview = preview_legacy_growth_file(path)
    if dry_run:
        return LegacyGrowthImportReport(
            preview,
            True,
            _file_sha256(path) == preview.file_sha256,
            tuple(_dry_run_report(repository, preview, run) for run in preview.runs),
        )
    actor_id = require_role(repository, actor, {Role.EDITOR, Role.ADMIN})
    if errors := tuple(error for run in preview.runs for error in run.errors):
        raise LegacyGrowthValidationError("; ".join(errors))
    make_id = id_factory or (lambda: str(uuid.uuid4()))
    source = _open_read_only(path)
    reports: list[LegacyGrowthRunImport] = []
    try:
        with repository.transaction():
            for run_preview in preview.runs:
                idempotency_key = _idempotency_key(preview.file_sha256, run_preview.run_id)
                existing = repository.plate_for_source(idempotency_key)
                legacy_collision = repository.plate_for_legacy_run_id(run_preview.run_id)
                if existing is not None or legacy_collision is not None:
                    reports.append(_dry_run_report(repository, preview, run_preview))
                    continue
                reports.append(
                    _import_run(
                        source,
                        repository,
                        actor_id,
                        preview,
                        run_preview,
                        make_id,
                    )
                )
            unchanged = _file_sha256(path) == preview.file_sha256
            if not unchanged:
                raise LegacyGrowthValidationError("Legacy source changed during import")
    finally:
        source.close()
    return LegacyGrowthImportReport(preview, False, True, tuple(reports))


def _import_run(
    source: sqlite3.Connection,
    repository: SqlPlateReaderRepository,
    actor_id: str,
    file_preview: LegacyGrowthFilePreview,
    run_preview: LegacyGrowthRunPreview,
    id_factory: Callable[[], str],
) -> LegacyGrowthRunImport:
    run_id = run_preview.run_id
    plate_meta = _one_run_row(source, "plate_meta", run_id)
    well_rows = _run_rows(source, "well_meta", run_id, "well")
    measurement_rows = _run_rows(source, "measurements", run_id, "signal_type, time_min, well")
    background_rows = (
        _run_rows(source, "backgrounds", run_id, "signal_type, time_min, bg_group")
        if _table_exists(source, "backgrounds")
        else []
    )
    provenance_rows = (
        _run_rows(source, "provenance", run_id, "timestamp, rowid")
        if _table_exists(source, "provenance")
        else []
    )
    experiment_id = ExperimentId(id_factory())
    plate_id = PlateId(id_factory())
    well_ids = {str(row["well"]).upper(): id_factory() for row in well_rows}
    created_at = _text(plate_meta.get("created_at"))
    date_value = _text(plate_meta.get("experiment_date") or plate_meta.get("exp_date"))
    if not date_value:
        raise LegacyGrowthValidationError(
            f"Legacy run {run_id} has no experiment date; refusing to invent one"
        )
    repository.create_experiment(
        {
            "experiment_id": experiment_id,
            "name": _text(plate_meta.get("experiment_name")) or f"Legacy run {run_id}",
            "experiment_date": date_value,
            "operator_name": _text(plate_meta.get("user_name") or plate_meta.get("user")),
            "custom_json": {"legacy_plate_meta": plate_meta},
            "created_by": actor_id,
            "created_at": created_at or None,
        }
    )
    channels = sorted({_text(row.get("signal_type")) or "od600" for row in measurement_rows})
    repository.create_plate(
        {
            "plate_id": plate_id,
            "experiment_id": experiment_id,
            "assay_type": AssayType.GROWTH,
            "plate_name": _text(plate_meta.get("experiment_name")) or run_id,
            "plate_format": 96,
            "channel": channels[0] if len(channels) == 1 else None,
            "legacy_run_id": run_id,
            "custom_json": {"legacy_plate_meta": plate_meta, "legacy_channels": channels},
            "created_by": actor_id,
            "created_at": created_at or None,
        }
    )
    repository.insert_wells(
        plate_id,
        [_well_record(row, well_ids[str(row["well"]).upper()]) for row in well_rows],
    )
    repository.insert_conditions(
        [_condition_record(row, well_ids[str(row["well"]).upper()]) for row in well_rows]
    )
    time_indices = _time_indices(measurement_rows)
    repository.insert_raw_observations(
        plate_id,
        [
            {
                "well_id": well_ids[str(row["well"]).upper()],
                "channel": _text(row.get("signal_type")) or "od600",
                "time_index": time_indices[
                    (_text(row.get("signal_type")) or "od600", _time_microseconds(row["time_min"]))
                ],
                "elapsed_microseconds": _time_microseconds(row["time_min"]),
                "value_raw": row.get("value_raw"),
            }
            for row in measurement_rows
        ],
    )
    repository.record_import_source(
        {
            "source_id": id_factory(),
            "plate_id": plate_id,
            "source_kind": "legacy_growth",
            "original_filename": file_preview.path.name,
            "content_sha256": file_preview.file_sha256,
            "byte_size": file_preview.byte_size,
            "parser_version": LEGACY_GROWTH_IMPORT_VERSION,
            "idempotency_key": _idempotency_key(file_preview.file_sha256, run_id),
            "status": "imported",
            "imported_by": actor_id,
            "custom_json": {
                "detected_version": file_preview.detected_version,
                "legacy_run_id": run_id,
            },
        }
    )
    if background_rows:
        _import_backgrounds(
            repository, plate_id, actor_id, run_preview.raw_sha256, background_rows, id_factory
        )
    repository.append_provenance(
        {
            "event_id": id_factory(),
            "actor_id": actor_id,
            "event_type": "legacy_growth_imported",
            "entity_type": "plate",
            "entity_id": plate_id,
            "details_json": {
                "legacy_run_id": run_id,
                "source_file_sha256": file_preview.file_sha256,
                "source_raw_sha256": run_preview.raw_sha256,
                "legacy_provenance": provenance_rows,
                "missing_target_fields": run_preview.missing_target_fields,
                "warnings": run_preview.warnings,
            },
        }
    )
    imported_hash = _destination_raw_hash(repository, plate_id)
    if imported_hash != run_preview.raw_sha256:
        raise LegacyGrowthValidationError(f"Raw verification failed for legacy run {run_id}")
    return LegacyGrowthRunImport(
        run_id,
        "imported",
        str(plate_id),
        run_preview.raw_sha256,
        imported_hash,
        _destination_counts(repository, plate_id),
        run_preview.warnings,
    )


def _preview_run(
    connection: sqlite3.Connection, plate_meta: dict[str, object]
) -> LegacyGrowthRunPreview:
    run_id = _text(plate_meta.get("run_id"))
    if not run_id:
        raise LegacyGrowthValidationError("Legacy plate metadata has no run_id")
    wells = _run_rows(connection, "well_meta", run_id, "well")
    measurements = _run_rows(connection, "measurements", run_id, "well, signal_type, time_min")
    backgrounds = (
        _run_rows(connection, "backgrounds", run_id, "bg_group, signal_type, time_min")
        if _table_exists(connection, "backgrounds")
        else []
    )
    warnings: list[str] = []
    errors: list[str] = []
    if not _text(plate_meta.get("experiment_date") or plate_meta.get("exp_date")):
        errors.append("Missing experiment date; migration will not invent one")
    positions: list[str] = []
    for row in wells:
        value = _text(row.get("well")).upper()
        try:
            positions.append(WellPosition.parse(value, PLATE_96).label)
        except ValueError:
            errors.append(f"Invalid 96-well position: {value}")
        custom, custom_warning = _custom_json(row.get("custom_json"))
        if custom_warning:
            warnings.append(f"{value}: {custom_warning}")
        inoculum = row.get("inoculum_size")
        if inoculum not in (None, "") and _optional_finite_float(inoculum) is None:
            warnings.append(f"{value}: invalid inoculum_size was mapped to null")
        replicate = custom.get("replicate")
        if replicate is not None and (
            isinstance(replicate, bool) or not isinstance(replicate, int) or replicate < 1
        ):
            warnings.append(f"{value}: invalid replicate was mapped to 1")
    if len(positions) != len(set(positions)):
        errors.append("Duplicate well metadata positions")
    if len(positions) != PLATE_96.well_count:
        warnings.append(f"Partial plate: found {len(positions)} of 96 wells")
    measurement_keys = [
        (
            _text(row.get("well")).upper(),
            _text(row.get("signal_type")) or "od600",
            _time_microseconds(row.get("time_min")),
        )
        for row in measurements
    ]
    if len(measurement_keys) != len(set(measurement_keys)):
        errors.append("Duplicate measurement identity")
    unknown_measurement_wells = sorted({key[0] for key in measurement_keys} - set(positions))
    if unknown_measurement_wells:
        errors.append(f"Measurements reference unknown wells: {unknown_measurement_wells}")
    missing = tuple(
        field
        for field in KNOWN_TARGET_GAPS
        if field not in plate_meta or plate_meta.get(field) in (None, "")
    )
    if missing:
        warnings.append("Legacy source has no values for: " + ", ".join(missing))
    return LegacyGrowthRunPreview(
        run_id=run_id,
        experiment_name=_text(plate_meta.get("experiment_name")) or f"Legacy run {run_id}",
        well_count=len(wells),
        measurement_count=len(measurements),
        background_count=len(backgrounds),
        raw_sha256=_legacy_raw_hash(measurements),
        missing_target_fields=missing,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _detect_version(connection: sqlite3.Connection) -> str:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if not tables >= REQUIRED_TABLES:
        raise LegacyGrowthValidationError(
            f"Unsupported legacy growth schema; missing tables: {sorted(REQUIRED_TABLES - tables)}"
        )
    plate_columns = _columns(connection, "plate_meta")
    well_columns = _columns(connection, "well_meta")
    measurement_columns = _columns(connection, "measurements")
    if not {"run_id", "experiment_name"} <= plate_columns:
        raise LegacyGrowthValidationError("Legacy plate_meta columns are unsupported")
    if not {"run_id", "well", "time_min", "signal_type", "value_raw"} <= measurement_columns:
        raise LegacyGrowthValidationError("Legacy measurement columns are unsupported")
    if not {"run_id", "well"} <= well_columns:
        raise LegacyGrowthValidationError("Legacy well_meta columns are unsupported")
    if {"experiment_date", "user_name", "app_version"} <= plate_columns:
        return "growth-sqlite-v4"
    if ("experiment_date" in plate_columns or "exp_date" in plate_columns) and (
        "user_name" in plate_columns or "user" in plate_columns
    ):
        return "growth-sqlite-pre-v4"
    raise LegacyGrowthValidationError("Legacy plate metadata version cannot be identified")


def _well_record(row: Mapping[str, object], well_id: str) -> dict[str, object]:
    position = WellPosition.parse(_text(row.get("well")).upper(), PLATE_96)
    custom, warning = _custom_json(row.get("custom_json"))
    if warning:
        custom = {"_legacy_custom_json_raw": _text(row.get("custom_json"))}
    return {
        "well_id": well_id,
        "position": position.label,
        "row_index": position.row_index,
        "column_index": position.column_index,
        "raw_label": _text(custom.get("raw_label")) or None,
        "display_name": _text(row.get("display_name")) or None,
        "is_blank": bool(row.get("is_blank")),
        "background_group": _text(row.get("bg_group")) or "plate",
        "plot_selected": bool(custom.get("plot", False)),
        "notes": _text(custom.get("notes")) or None,
        "custom_json": custom,
    }


def _condition_record(row: Mapping[str, object], well_id: str) -> dict[str, object]:
    custom, _warning = _custom_json(row.get("custom_json"))
    inoculum = _optional_finite_float(row.get("inoculum_size"))
    replicate = custom.get("replicate", 1)
    if isinstance(replicate, bool) or not isinstance(replicate, int) or replicate < 1:
        replicate = 1
    return {
        "well_id": well_id,
        "strain": _text(row.get("strain")) or None,
        "medium": _text(row.get("media")) or None,
        "replicate": replicate,
        "inoculum_size": inoculum,
        "treatment": _text(row.get("treatments")) or None,
    }


def _import_backgrounds(
    repository: SqlPlateReaderRepository,
    plate_id: PlateId,
    actor_id: str,
    input_sha256: str,
    rows: Sequence[dict[str, object]],
    id_factory: Callable[[], str],
) -> None:
    revision_id = RevisionId(id_factory())
    repository.add_analysis_revision(
        {
            "revision_id": revision_id,
            "plate_id": plate_id,
            "assay_type": AssayType.GROWTH,
            "algorithm_name": "growth_background",
            "algorithm_version": "growth-background/legacy-v4",
            "parameters_json": {"source": "stored legacy backgrounds"},
            "input_sha256": input_sha256,
            "created_by": actor_id,
        }
    )
    times = _time_indices(rows, channel_key="signal_type", time_key="time_min")
    repository.insert_growth_backgrounds(
        revision_id,
        [
            {
                "background_group": _text(row.get("bg_group")) or "plate",
                "channel": _text(row.get("signal_type")) or "od600",
                "time_index": times[
                    (_text(row.get("signal_type")) or "od600", _time_microseconds(row["time_min"]))
                ],
                "elapsed_microseconds": _time_microseconds(row["time_min"]),
                "mean_value": row["bg_mean"],
                "std_value": row.get("bg_std"),
                "coefficient_of_variation": row.get("bg_cv"),
                "blank_count": _positive_int(row.get("n_blank_wells"), "n_blank_wells"),
                "qc_status": _qc_status(row.get("bg_cv")),
            }
            for row in rows
        ],
    )


def _legacy_raw_hash(rows: Sequence[Mapping[str, object]]) -> str:
    payload = sorted(
        (
            _text(row.get("well")).upper(),
            _text(row.get("signal_type")) or "od600",
            _time_microseconds(row.get("time_min")),
            row.get("value_raw"),
        )
        for row in rows
    )
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _destination_raw_hash(repository: SqlPlateReaderRepository, plate_id: PlateId) -> str:
    rows = repository.connection.execute(
        "SELECT w.position, gm.channel, gm.elapsed_microseconds, gm.value_raw "
        "FROM growth_measurements gm JOIN wells w ON w.well_id = gm.well_id "
        "WHERE gm.plate_id = ? ORDER BY w.position, gm.channel, gm.elapsed_microseconds",
        (plate_id,),
    ).fetchall()
    payload = sorted((str(row[0]), str(row[1]), int(row[2]), row[3]) for row in rows)
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _destination_counts(repository: SqlPlateReaderRepository, plate_id: PlateId) -> dict[str, int]:
    return {
        "wells": _query_count(
            repository,
            "SELECT count(*) FROM wells WHERE plate_id = ?",
            plate_id,
        ),
        "measurements": _query_count(
            repository,
            "SELECT count(*) FROM growth_measurements WHERE plate_id = ?",
            plate_id,
        ),
        "backgrounds": _query_count(
            repository,
            "SELECT count(*) FROM growth_backgrounds WHERE revision_id IN "
            "(SELECT revision_id FROM analysis_revisions WHERE plate_id = ?)",
            plate_id,
        ),
    }


def _query_count(repository: SqlPlateReaderRepository, sql: str, plate_id: PlateId) -> int:
    row = repository.connection.execute(sql, (plate_id,)).fetchone()
    if row is None:
        raise LegacyGrowthValidationError("Count query returned no result")
    return int(str(row[0]))


def _dry_run_report(
    repository: SqlPlateReaderRepository,
    file_preview: LegacyGrowthFilePreview,
    run: LegacyGrowthRunPreview,
) -> LegacyGrowthRunImport:
    idempotency_key = _idempotency_key(file_preview.file_sha256, run.run_id)
    existing = repository.plate_for_source(idempotency_key)
    legacy_collision = repository.plate_for_legacy_run_id(run.run_id)
    plate_id = existing or legacy_collision
    warnings = list(run.warnings)
    if existing is not None:
        status = "skipped_duplicate_source"
        warnings.append("This exact source and legacy run were already imported.")
    elif legacy_collision is not None:
        status = "skipped_duplicate_run_id"
        warnings.append(
            "The legacy run ID already exists from another source; the default policy skips it."
        )
    else:
        status = "blocked" if run.errors else "ready"
    if plate_id is None:
        counts = {
            "wells": run.well_count,
            "measurements": run.measurement_count,
            "backgrounds": run.background_count,
        }
        imported_hash = None
    else:
        counts = _destination_counts(repository, plate_id)
        imported_hash = _destination_raw_hash(repository, plate_id)
        if imported_hash != run.raw_sha256:
            warnings.append(
                "The existing run has a different raw hash; review before choosing any "
                "future explicit versioning workflow."
            )
    return LegacyGrowthRunImport(
        run.run_id,
        status,
        None if plate_id is None else str(plate_id),
        run.raw_sha256,
        imported_hash,
        counts,
        tuple(warnings),
    )


def _time_indices(
    rows: Sequence[Mapping[str, object]],
    *,
    channel_key: str = "signal_type",
    time_key: str = "time_min",
) -> dict[tuple[str, int], int]:
    times_by_channel: dict[str, set[int]] = {}
    for row in rows:
        channel = _text(row.get(channel_key)) or "od600"
        times_by_channel.setdefault(channel, set()).add(_time_microseconds(row.get(time_key)))
    return {
        (channel, elapsed): index
        for channel, values in times_by_channel.items()
        for index, elapsed in enumerate(sorted(values))
    }


def _custom_json(value: object) -> tuple[dict[str, object], str | None]:
    if value in (None, ""):
        return {}, None
    if not isinstance(value, str):
        return {}, "custom_json is not text and was preserved as a raw string"
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}, "custom_json is invalid and was preserved as a raw string"
    if not isinstance(parsed, dict):
        return {}, "custom_json is not an object and was preserved as a raw string"
    return {str(key): item for key, item in parsed.items()}, None


def _optional_finite_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(str(value))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LegacyGrowthValidationError(f"Legacy {field} must be a positive integer")
    return value


def _qc_status(value: object) -> str:
    cv = _optional_finite_float(value)
    if cv is None:
        return "missing"
    if cv < 0.05:
        return "good"
    if cv < 0.10:
        return "caution"
    return "high_cv"


def _idempotency_key(file_sha256: str, run_id: str) -> str:
    return f"legacy_growth_sqlite:{file_sha256}:{run_id}:{LEGACY_GROWTH_IMPORT_VERSION}"


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _one_run_row(connection: sqlite3.Connection, table: str, run_id: str) -> dict[str, object]:
    rows = _dict_rows(connection, f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))
    if len(rows) != 1:
        raise LegacyGrowthValidationError(f"Expected one {table} row for {run_id}")
    return rows[0]


def _run_rows(
    connection: sqlite3.Connection, table: str, run_id: str, order_by: str
) -> list[dict[str, object]]:
    return _dict_rows(
        connection, f"SELECT * FROM {table} WHERE run_id = ? ORDER BY {order_by}", (run_id,)
    )


def _dict_rows(
    connection: sqlite3.Connection, sql: str, parameters: Sequence[object] = ()
) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _time_microseconds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LegacyGrowthValidationError("Legacy time_min must be numeric")
    minutes = float(value)
    if not math.isfinite(minutes) or minutes < 0:
        raise LegacyGrowthValidationError("Legacy time_min must be finite and nonnegative")
    return round(minutes * 60_000_000)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
