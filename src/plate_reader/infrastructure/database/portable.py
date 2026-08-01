"""Standard-SQLite portable exports, validation, and complete logical backups."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from plate_reader.application.ports.portable import (
    PortableImportPreviewData,
    PortableImportResultData,
)
from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.infrastructure.database.migrations import apply_migrations
from plate_reader.infrastructure.database.transactions import transaction

PORTABLE_FORMAT_VERSION = 1
SCHEMA_VERSION = 1
PORTABLE_PARSER_VERSION = "portable/1.0.0"


class SqlitePortableRunExporter:
    """Materialize one portable artifact without exposing temp paths to the UI."""

    def __init__(
        self,
        connection: Connection,
        migrations_directory: Path,
        *,
        exporter_version: str,
    ) -> None:
        self.connection = connection
        self.migrations_directory = migrations_directory
        self.exporter_version = exporter_version

    def export(self, plate_ids: Sequence[str], revision_ids: Sequence[str]) -> tuple[str, bytes]:
        filename = f"plate-reader-export-{uuid.uuid4()}.sqlite"
        with tempfile.TemporaryDirectory(prefix="plate-reader-export-") as directory:
            destination = Path(directory) / filename
            export_portable_runs(
                self.connection,
                destination,
                self.migrations_directory,
                plate_ids,
                revision_ids=revision_ids,
                exporter_version=self.exporter_version,
            )
            return filename, destination.read_bytes()


class SqlitePortableRunImporter:
    """Validate/import uploads through short-lived standard-SQLite files."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def preview(self, content: bytes) -> PortableImportPreviewData:
        with _materialize_portable_upload(content) as path:
            preview = preview_portable_import(self.connection, path)
            return PortableImportPreviewData(
                preview.source.export_id,
                preview.source.file_sha256,
                preview.source.plate_ids,
                preview.source.revision_ids,
                preview.source.table_counts,
                preview.collisions,
            )

    def import_content(
        self, content: bytes, *, actor_id: str, collision_policy: str
    ) -> PortableImportResultData:
        with _materialize_portable_upload(content) as path:
            report = import_portable_file(
                self.connection,
                path,
                actor_id=actor_id,
                collision_policy=collision_policy,
            )
            return PortableImportResultData(
                report.export_id,
                report.file_sha256,
                report.created,
                report.table_counts,
                report.collisions,
                report.plate_id_map,
                report.revision_id_map,
            )


@contextmanager
def _materialize_portable_upload(content: bytes) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="plate-reader-portable-upload-") as directory:
        path = Path(directory) / "upload.plate-reader.sqlite"
        path.write_bytes(content)
        yield path


TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": (
        "user_id",
        "email",
        "display_name",
        "role",
        "is_active",
        "created_at",
        "updated_at",
    ),
    "experiments": (
        "experiment_id",
        "name",
        "project",
        "experiment_date",
        "operator_name",
        "reader",
        "incubation_time_hours",
        "inoculum_od",
        "growth_phase",
        "harvest_od",
        "doubling_time_minutes",
        "notes",
        "custom_json",
        "created_by",
        "created_at",
        "updated_at",
    ),
    "experiment_tags": ("experiment_id", "tag"),
    "plates": (
        "plate_id",
        "experiment_id",
        "assay_type",
        "plate_name",
        "plate_format",
        "lifecycle_status",
        "instrument",
        "channel",
        "temperature",
        "temperature_unit",
        "manual_subtraction",
        "threshold",
        "threshold_method",
        "background_method",
        "is_locked",
        "is_checked",
        "legacy_run_id",
        "custom_json",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
        "deleted_by",
    ),
    "wells": (
        "well_id",
        "plate_id",
        "position",
        "row_index",
        "column_index",
        "raw_label",
        "display_name",
        "is_blank",
        "background_group",
        "plot_selected",
        "notes",
        "custom_json",
        "created_at",
        "updated_at",
    ),
    "well_conditions": (
        "well_id",
        "strain",
        "medium",
        "replicate",
        "inoculum_size",
        "inoculum_unit",
        "grouping_label",
        "treatment",
        "concentration",
        "concentration_unit",
        "custom_json",
    ),
    "import_sources": (
        "source_id",
        "plate_id",
        "source_kind",
        "original_filename",
        "content_sha256",
        "byte_size",
        "parser_version",
        "idempotency_key",
        "status",
        "imported_by",
        "imported_at",
        "custom_json",
    ),
    "growth_measurements": (
        "plate_id",
        "well_id",
        "channel",
        "time_index",
        "elapsed_microseconds",
        "value_raw",
    ),
    "analysis_revisions": (
        "revision_id",
        "plate_id",
        "assay_type",
        "algorithm_name",
        "algorithm_version",
        "parameters_json",
        "input_sha256",
        "is_current",
        "created_by",
        "created_at",
    ),
    "growth_backgrounds": (
        "revision_id",
        "background_group",
        "channel",
        "time_index",
        "elapsed_microseconds",
        "mean_value",
        "std_value",
        "coefficient_of_variation",
        "blank_count",
        "qc_status",
    ),
    "growth_metrics": (
        "revision_id",
        "well_id",
        "channel",
        "metric_name",
        "metric_value",
        "metric_unit",
        "quality_flag",
    ),
    "mic_readings": ("plate_id", "well_id", "channel", "value_raw"),
    "mic_well_calls": (
        "revision_id",
        "well_id",
        "background_value",
        "value_background_subtracted",
        "growth_call",
    ),
    "mic_results": (
        "result_id",
        "revision_id",
        "group_key",
        "strain",
        "treatment",
        "medium",
        "replicate",
        "mic_value",
        "mic_operator",
        "mic_unit",
        "threshold_used",
        "lowest_tested_concentration",
        "highest_tested_concentration",
        "concentrations_json",
        "point_count",
        "calculation_status",
        "warning",
    ),
    "plate_templates": (
        "template_id",
        "template_name",
        "assay_type",
        "layout_json",
        "created_by",
        "created_at",
        "updated_at",
    ),
    "saved_options": ("option_type", "value", "created_by", "created_at"),
    "provenance_events": (
        "event_id",
        "actor_id",
        "event_type",
        "entity_type",
        "entity_id",
        "occurred_at",
        "details_json",
    ),
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "users": ("user_id",),
    "experiments": ("experiment_id",),
    "experiment_tags": ("experiment_id", "tag"),
    "plates": ("plate_id",),
    "wells": ("well_id",),
    "well_conditions": ("well_id",),
    "import_sources": ("source_id",),
    "growth_measurements": ("plate_id", "well_id", "channel", "time_index"),
    "analysis_revisions": ("revision_id",),
    "growth_backgrounds": (
        "revision_id",
        "background_group",
        "channel",
        "time_index",
    ),
    "growth_metrics": ("revision_id", "well_id", "channel", "metric_name"),
    "mic_readings": ("plate_id", "well_id", "channel"),
    "mic_well_calls": ("revision_id", "well_id"),
    "mic_results": ("result_id",),
    "plate_templates": ("template_id",),
    "saved_options": ("option_type", "value"),
    "provenance_events": ("event_id",),
}

PORTABLE_DATA_TABLES = (
    "users",
    "experiments",
    "experiment_tags",
    "plates",
    "wells",
    "well_conditions",
    "import_sources",
    "growth_measurements",
    "analysis_revisions",
    "growth_backgrounds",
    "growth_metrics",
    "mic_readings",
    "mic_well_calls",
    "mic_results",
    "provenance_events",
)

TRIGGER_NAMES = {
    "prevent_growth_measurement_update",
    "prevent_growth_measurement_delete",
    "prevent_mic_reading_update",
    "prevent_mic_reading_delete",
    "prevent_provenance_update",
    "prevent_provenance_delete",
}


class PortableValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PortableExportReport:
    path: Path
    export_id: str
    file_sha256: str
    table_counts: dict[str, int]
    table_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class PortablePreview:
    path: Path
    export_id: str
    file_sha256: str
    plate_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    table_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class PortableImportPreview:
    source: PortablePreview
    collisions: dict[str, int]


@dataclass(frozen=True, slots=True)
class PortableImportReport:
    export_id: str
    file_sha256: str
    created: bool
    table_counts: dict[str, int]
    collisions: dict[str, int]
    plate_id_map: dict[str, str]
    revision_id_map: dict[str, str]


@dataclass(frozen=True, slots=True)
class CompleteRestoreReport:
    path: Path
    table_counts: dict[str, int]
    table_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class CompleteConnectionRestoreReport:
    table_counts: dict[str, int]
    table_sha256: dict[str, str]


def export_portable_runs(
    source: Connection,
    destination: Path,
    migrations_directory: Path,
    plate_ids: Sequence[str],
    *,
    revision_ids: Sequence[str] = (),
    exporter_version: str,
    id_factory: Callable[[], str] | None = None,
    exported_at: str | None = None,
) -> PortableExportReport:
    selected_plates = tuple(dict.fromkeys(plate_ids))
    if not selected_plates:
        raise PortableValidationError("At least one plate must be selected")
    if destination.exists():
        raise FileExistsError(destination)
    export_id = (id_factory or (lambda: str(uuid.uuid4())))()
    timestamp = exported_at or datetime.now(UTC).isoformat()
    selection = _selection(source, selected_plates, revision_ids)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target_sqlite = sqlite3.connect(destination, isolation_level=None)
    target = cast(Connection, target_sqlite)
    try:
        apply_migrations(target, migrations_directory)
        with transaction(target):
            _copy_selection(source, target, selection)
            _create_portable_manifest_tables(target)
            counts: dict[str, int] = {}
            hashes: dict[str, str] = {}
            for table in PORTABLE_DATA_TABLES:
                count, digest = logical_table_hash(target, table)
                counts[table] = count
                hashes[table] = digest
                target.execute(
                    "INSERT INTO portable_table_checksums(table_name, row_count, sha256) "
                    "VALUES (?, ?, ?)",
                    (table, count, digest),
                )
            target.execute(
                "INSERT INTO portable_manifest "
                "(export_id, format_version, schema_version, exported_at, exporter_version, "
                "plate_ids_json, revision_ids_json, content_hash_algorithm) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    export_id,
                    PORTABLE_FORMAT_VERSION,
                    SCHEMA_VERSION,
                    timestamp,
                    exporter_version,
                    _canonical_json(selection.plate_ids),
                    _canonical_json(selection.revision_ids),
                    "sha256-canonical-json-lines-v1",
                ),
            )
        target_sqlite.execute("VACUUM")
    except BaseException:
        target_sqlite.close()
        destination.unlink(missing_ok=True)
        raise
    target_sqlite.close()
    return PortableExportReport(
        path=destination,
        export_id=export_id,
        file_sha256=_file_sha256(destination),
        table_counts=counts,
        table_sha256=hashes,
    )


def validate_portable_file(path: Path) -> PortablePreview:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise PortableValidationError("SQLite integrity check failed")
        _validate_schema_objects(connection)
        manifest_rows = connection.execute(
            "SELECT export_id, format_version, schema_version, plate_ids_json, "
            "revision_ids_json, content_hash_algorithm FROM portable_manifest"
        ).fetchall()
        if len(manifest_rows) != 1:
            raise PortableValidationError("Portable manifest must contain exactly one row")
        export_id, format_version, schema_version, plate_json, revision_json, algorithm = (
            manifest_rows[0]
        )
        if format_version != PORTABLE_FORMAT_VERSION or schema_version != SCHEMA_VERSION:
            raise PortableValidationError("Unsupported portable or schema version")
        if algorithm != "sha256-canonical-json-lines-v1":
            raise PortableValidationError("Unsupported portable checksum algorithm")
        expected = {
            str(row[0]): (int(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT table_name, row_count, sha256 FROM portable_table_checksums"
            )
        }
        if set(expected) != set(PORTABLE_DATA_TABLES):
            raise PortableValidationError("Portable table checksum set is incomplete")
        counts: dict[str, int] = {}
        generic = cast(Connection, connection)
        for table in PORTABLE_DATA_TABLES:
            count, digest = logical_table_hash(generic, table)
            counts[table] = count
            if expected[table] != (count, digest):
                raise PortableValidationError(f"Portable checksum mismatch: {table}")
        plate_ids = _json_string_tuple(str(plate_json), "plate_ids_json")
        revision_ids = _json_string_tuple(str(revision_json), "revision_ids_json")
        actual_plate_ids = tuple(
            str(row[0])
            for row in connection.execute("SELECT plate_id FROM plates ORDER BY plate_id")
        )
        if tuple(sorted(plate_ids)) != actual_plate_ids:
            raise PortableValidationError("Manifest plate IDs do not match exported rows")
        return PortablePreview(
            path=path,
            export_id=str(export_id),
            file_sha256=_file_sha256(path),
            plate_ids=plate_ids,
            revision_ids=revision_ids,
            table_counts=counts,
        )
    except sqlite3.Error as error:
        raise PortableValidationError("Portable file is not a valid version-1 database") from error
    finally:
        connection.close()


def preview_portable_import(destination: Connection, path: Path) -> PortableImportPreview:
    preview = validate_portable_file(path)
    source_sqlite = _open_portable_read_only(path)
    try:
        collisions: dict[str, int] = {}
        for table, id_column in _ID_COLUMNS.items():
            incoming = source_sqlite.execute(
                f"SELECT {id_column} FROM {table} ORDER BY {id_column}"
            ).fetchall()
            collisions[table] = sum(
                destination.execute(
                    f"SELECT 1 FROM {table} WHERE {id_column} = ?", (row[0],)
                ).fetchone()
                is not None
                for row in incoming
            )
        return PortableImportPreview(preview, collisions)
    finally:
        source_sqlite.close()


def import_portable_file(
    destination: Connection,
    path: Path,
    *,
    actor_id: str,
    collision_policy: str = "remap",
    id_factory: Callable[[], str] | None = None,
    imported_at: str | None = None,
) -> PortableImportReport:
    if collision_policy not in {"remap", "error"}:
        raise ValueError("collision_policy must be 'remap' or 'error'")
    import_preview = preview_portable_import(destination, path)
    preview = import_preview.source
    if collision_policy == "error" and any(import_preview.collisions.values()):
        raise PortableValidationError("Portable import has identifier collisions")
    actor = destination.execute(
        "SELECT is_active, role FROM users WHERE user_id = ?", (actor_id,)
    ).fetchone()
    if actor is None or not bool(actor[0]) or str(actor[1]) not in {"editor", "admin"}:
        raise PermissionError("Portable import requires an active editor or admin")
    idempotency_key = f"portable:{preview.file_sha256}:{preview.export_id}"
    existing = destination.execute(
        "SELECT custom_json FROM import_sources WHERE idempotency_key = ? AND status = 'imported'",
        (idempotency_key,),
    ).fetchone()
    if existing is not None:
        details = json.loads(str(existing[0]))
        return PortableImportReport(
            export_id=preview.export_id,
            file_sha256=preview.file_sha256,
            created=False,
            table_counts=preview.table_counts,
            collisions=import_preview.collisions,
            plate_id_map={str(key): str(value) for key, value in details["plate_id_map"].items()},
            revision_id_map={
                str(key): str(value) for key, value in details["revision_id_map"].items()
            },
        )

    create_id = id_factory or (lambda: str(uuid.uuid4()))
    timestamp = imported_at or datetime.now(UTC).isoformat()
    source_sqlite = _open_portable_read_only(path)
    try:
        data = {table: _read_dict_rows(source_sqlite, table) for table in PORTABLE_DATA_TABLES}
    finally:
        source_sqlite.close()

    user_map, users_to_insert = _map_import_users(destination, data["users"], create_id)
    experiment_map = _allocate_id_map(
        destination, "experiments", "experiment_id", data["experiments"], create_id
    )
    plate_map = _allocate_id_map(destination, "plates", "plate_id", data["plates"], create_id)
    well_map = _allocate_id_map(destination, "wells", "well_id", data["wells"], create_id)
    source_map = _allocate_id_map(
        destination, "import_sources", "source_id", data["import_sources"], create_id
    )
    revision_map = _allocate_id_map(
        destination,
        "analysis_revisions",
        "revision_id",
        data["analysis_revisions"],
        create_id,
    )
    result_map = _allocate_id_map(
        destination, "mic_results", "result_id", data["mic_results"], create_id
    )
    event_map = _allocate_id_map(
        destination, "provenance_events", "event_id", data["provenance_events"], create_id
    )
    transformed = _transform_import_rows(
        destination,
        data,
        user_map=user_map,
        experiment_map=experiment_map,
        plate_map=plate_map,
        well_map=well_map,
        source_map=source_map,
        revision_map=revision_map,
        result_map=result_map,
        event_map=event_map,
        export_id=preview.export_id,
    )
    transformed["users"] = users_to_insert
    details = {
        "export_id": preview.export_id,
        "plate_id_map": plate_map,
        "revision_id_map": revision_map,
        "table_counts": preview.table_counts,
    }
    with transaction(destination):
        for table in PORTABLE_DATA_TABLES:
            _insert_dict_rows(destination, table, transformed[table])
        imported_plate_ids = tuple(plate_map.values())
        destination.execute(
            "INSERT INTO import_sources "
            "(source_id, plate_id, source_kind, original_filename, content_sha256, "
            "byte_size, parser_version, idempotency_key, status, imported_by, imported_at, "
            "custom_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                create_id(),
                imported_plate_ids[0] if imported_plate_ids else None,
                "portable",
                path.name,
                preview.file_sha256,
                path.stat().st_size,
                PORTABLE_PARSER_VERSION,
                idempotency_key,
                "imported",
                actor_id,
                timestamp,
                _canonical_json(details),
            ),
        )
        destination.execute(
            "INSERT INTO provenance_events "
            "(event_id, actor_id, event_type, entity_type, entity_id, occurred_at, "
            "details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                create_id(),
                actor_id,
                "portable_imported",
                "plate",
                imported_plate_ids[0],
                timestamp,
                _canonical_json(details),
            ),
        )
    return PortableImportReport(
        export_id=preview.export_id,
        file_sha256=preview.file_sha256,
        created=True,
        table_counts=preview.table_counts,
        collisions=import_preview.collisions,
        plate_id_map=plate_map,
        revision_id_map=revision_map,
    )


def backup_complete_database(
    source: Connection, destination: Path, migrations_directory: Path
) -> Path:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target_sqlite = sqlite3.connect(destination, isolation_level=None)
    target = cast(Connection, target_sqlite)
    try:
        apply_migrations(target, migrations_directory)
        if source.in_transaction:
            raise RuntimeError("Complete backup requires an idle source connection")
        source.execute("BEGIN")
        try:
            with transaction(target):
                for table in TABLE_COLUMNS:
                    _copy_table_in_chunks(source, target, table)
        finally:
            source.rollback()
        target_sqlite.execute("VACUUM")
        if target_sqlite.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise PortableValidationError("Backup integrity check failed")
    except BaseException:
        target_sqlite.close()
        destination.unlink(missing_ok=True)
        raise
    target_sqlite.close()
    return destination


def restore_complete_database(
    backup: Path, destination: Path, migrations_directory: Path
) -> CompleteRestoreReport:
    if destination.exists():
        raise FileExistsError(destination)
    source = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
    target_sqlite: sqlite3.Connection | None = None
    try:
        _validate_complete_backup(source)
        counts: dict[str, int] = {}
        hashes: dict[str, str] = {}
        for table in TABLE_COLUMNS:
            counts[table], hashes[table] = logical_table_hash(cast(Connection, source), table)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target_sqlite = sqlite3.connect(destination, isolation_level=None)
        target = cast(Connection, target_sqlite)
        apply_migrations(target, migrations_directory)
        with transaction(target):
            for table in TABLE_COLUMNS:
                _copy_table_in_chunks(cast(Connection, source), target, table)
            for table in TABLE_COLUMNS:
                restored_count, restored_hash = logical_table_hash(target, table)
                if restored_count != counts[table] or restored_hash != hashes[table]:
                    raise PortableValidationError(f"Restored table verification failed: {table}")
        if target_sqlite.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise PortableValidationError("Restored database integrity check failed")
        target_sqlite.close()
        target_sqlite = None
        return CompleteRestoreReport(destination, counts, hashes)
    except BaseException:
        if target_sqlite is not None:
            target_sqlite.close()
        destination.unlink(missing_ok=True)
        raise
    finally:
        source.close()


def restore_complete_database_to_connection(
    backup: Path, target: Connection
) -> CompleteConnectionRestoreReport:
    """Restore a verified complete backup into an empty, migrated connection."""

    source = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
    try:
        _validate_complete_backup(source)
        nonempty = [
            table
            for table in TABLE_COLUMNS
            if target.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        ]
        if nonempty:
            raise PortableValidationError(
                "Remote restore target is not empty; refusing to overwrite application data"
            )
        counts: dict[str, int] = {}
        hashes: dict[str, str] = {}
        for table in TABLE_COLUMNS:
            counts[table], hashes[table] = logical_table_hash(cast(Connection, source), table)
        with transaction(target):
            for table in TABLE_COLUMNS:
                _copy_table_in_chunks(cast(Connection, source), target, table)
            for table in TABLE_COLUMNS:
                restored_count, restored_hash = logical_table_hash(target, table)
                if restored_count != counts[table] or restored_hash != hashes[table]:
                    raise PortableValidationError(f"Restored table verification failed: {table}")
        return CompleteConnectionRestoreReport(counts, hashes)
    finally:
        source.close()


_ID_COLUMNS = {
    "users": "user_id",
    "experiments": "experiment_id",
    "plates": "plate_id",
    "wells": "well_id",
    "import_sources": "source_id",
    "analysis_revisions": "revision_id",
    "mic_results": "result_id",
    "provenance_events": "event_id",
}


@dataclass(frozen=True, slots=True)
class _Selection:
    plate_ids: tuple[str, ...]
    experiment_ids: tuple[str, ...]
    well_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    user_ids: tuple[str, ...]
    provenance_entity_ids: tuple[str, ...]


def _selection(
    source: Connection, plate_ids: tuple[str, ...], revision_ids: Sequence[str]
) -> _Selection:
    found = tuple(
        str(row[0])
        for row in source.execute(
            f"SELECT plate_id FROM plates WHERE {_in_clause('plate_id', plate_ids)} "
            "ORDER BY plate_id",
            plate_ids,
        ).fetchall()
    )
    if set(found) != set(plate_ids):
        missing = sorted(set(plate_ids) - set(found))
        raise PortableValidationError(f"Unknown plate IDs: {missing}")
    experiment_ids = _column_values(
        source,
        f"SELECT DISTINCT experiment_id FROM plates WHERE {_in_clause('plate_id', plate_ids)}",
        plate_ids,
    )
    well_ids = _column_values(
        source,
        f"SELECT well_id FROM wells WHERE {_in_clause('plate_id', plate_ids)}",
        plate_ids,
    )
    if revision_ids:
        selected_revisions = tuple(dict.fromkeys(revision_ids))
        valid_revisions = _column_values(
            source,
            f"SELECT revision_id FROM analysis_revisions WHERE "
            f"{_in_clause('revision_id', selected_revisions)} AND "
            f"{_in_clause('plate_id', plate_ids)}",
            (*selected_revisions, *plate_ids),
        )
        if set(valid_revisions) != set(selected_revisions):
            raise PortableValidationError(
                "A selected revision is absent or belongs to another plate"
            )
    else:
        selected_revisions = _column_values(
            source,
            f"SELECT revision_id FROM analysis_revisions WHERE {_in_clause('plate_id', plate_ids)}",
            plate_ids,
        )
    entity_ids = tuple(dict.fromkeys((*experiment_ids, *plate_ids, *well_ids, *selected_revisions)))
    actor_queries = (
        (
            "SELECT created_by FROM experiments WHERE "
            + _in_clause("experiment_id", experiment_ids),
            experiment_ids,
        ),
        ("SELECT created_by FROM plates WHERE " + _in_clause("plate_id", plate_ids), plate_ids),
        ("SELECT deleted_by FROM plates WHERE " + _in_clause("plate_id", plate_ids), plate_ids),
        (
            "SELECT imported_by FROM import_sources WHERE " + _in_clause("plate_id", plate_ids),
            plate_ids,
        ),
        (
            "SELECT created_by FROM analysis_revisions WHERE "
            + _in_clause("revision_id", selected_revisions),
            selected_revisions,
        ),
        (
            "SELECT actor_id FROM provenance_events WHERE " + _in_clause("entity_id", entity_ids),
            entity_ids,
        ),
    )
    user_ids: list[str] = []
    for sql, parameters in actor_queries:
        user_ids.extend(_column_values(source, sql, parameters, omit_null=True))
    return _Selection(
        plate_ids=tuple(sorted(plate_ids)),
        experiment_ids=tuple(sorted(experiment_ids)),
        well_ids=tuple(sorted(well_ids)),
        revision_ids=tuple(sorted(selected_revisions)),
        user_ids=tuple(sorted(set(user_ids))),
        provenance_entity_ids=tuple(sorted(entity_ids)),
    )


def _copy_selection(source: Connection, target: Connection, selection: _Selection) -> None:
    filters: dict[str, tuple[str, Sequence[object]]] = {
        "users": (_in_clause("user_id", selection.user_ids), selection.user_ids),
        "experiments": (
            _in_clause("experiment_id", selection.experiment_ids),
            selection.experiment_ids,
        ),
        "experiment_tags": (
            _in_clause("experiment_id", selection.experiment_ids),
            selection.experiment_ids,
        ),
        "plates": (_in_clause("plate_id", selection.plate_ids), selection.plate_ids),
        "wells": (_in_clause("plate_id", selection.plate_ids), selection.plate_ids),
        "well_conditions": (_in_clause("well_id", selection.well_ids), selection.well_ids),
        "import_sources": (_in_clause("plate_id", selection.plate_ids), selection.plate_ids),
        "growth_measurements": (_in_clause("plate_id", selection.plate_ids), selection.plate_ids),
        "analysis_revisions": (
            _in_clause("revision_id", selection.revision_ids),
            selection.revision_ids,
        ),
        "growth_backgrounds": (
            _in_clause("revision_id", selection.revision_ids),
            selection.revision_ids,
        ),
        "growth_metrics": (
            _in_clause("revision_id", selection.revision_ids),
            selection.revision_ids,
        ),
        "mic_readings": (_in_clause("plate_id", selection.plate_ids), selection.plate_ids),
        "mic_well_calls": (
            _in_clause("revision_id", selection.revision_ids),
            selection.revision_ids,
        ),
        "mic_results": (
            _in_clause("revision_id", selection.revision_ids),
            selection.revision_ids,
        ),
        "provenance_events": (
            _in_clause("entity_id", selection.provenance_entity_ids),
            selection.provenance_entity_ids,
        ),
    }
    for table in PORTABLE_DATA_TABLES:
        where, parameters = filters[table]
        rows = _select_rows(source, table, where, parameters)
        _insert_rows(target, table, rows)


def logical_table_hash(connection: Connection, table: str) -> tuple[int, str]:
    columns = TABLE_COLUMNS[table]
    order = PRIMARY_KEYS[table]
    cursor = connection.execute(
        f"SELECT {_column_sql(columns)} FROM {table} ORDER BY {_column_sql(order)}"
    )
    digest = hashlib.sha256()
    count = 0
    while rows := cursor.fetchmany(1_000):
        for row in rows:
            digest.update(_canonical_json(row).encode())
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


def _select_rows(
    connection: Connection,
    table: str,
    where: str = "1 = 1",
    parameters: Sequence[object] = (),
) -> list[tuple[object, ...]]:
    rows = connection.execute(
        f"SELECT {_column_sql(TABLE_COLUMNS[table])} FROM {table} WHERE {where} "
        f"ORDER BY {_column_sql(PRIMARY_KEYS[table])}",
        parameters,
    ).fetchall()
    return [tuple(row) for row in rows]


def _insert_rows(connection: Connection, table: str, rows: Iterable[Sequence[object]]) -> None:
    materialized = tuple(rows)
    if not materialized:
        return
    columns = TABLE_COLUMNS[table]
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({_column_sql(columns)}) VALUES ({placeholders})", materialized
    )


def _copy_table_in_chunks(
    source: Connection,
    target: Connection,
    table: str,
    *,
    batch_size: int = 1_000,
) -> None:
    cursor = source.execute(
        f"SELECT {_column_sql(TABLE_COLUMNS[table])} FROM {table} "
        f"ORDER BY {_column_sql(PRIMARY_KEYS[table])}"
    )
    while rows := cursor.fetchmany(batch_size):
        _insert_rows(target, table, rows)


def _create_portable_manifest_tables(connection: Connection) -> None:
    connection.execute(
        "CREATE TABLE portable_manifest ("
        "export_id TEXT PRIMARY KEY, format_version INTEGER NOT NULL, "
        "schema_version INTEGER NOT NULL, exported_at TEXT NOT NULL, "
        "exporter_version TEXT NOT NULL, plate_ids_json TEXT NOT NULL, "
        "revision_ids_json TEXT NOT NULL, content_hash_algorithm TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE portable_table_checksums ("
        "table_name TEXT PRIMARY KEY, row_count INTEGER NOT NULL, sha256 TEXT NOT NULL)"
    )


def _open_portable_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _read_dict_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    columns = TABLE_COLUMNS[table]
    rows = connection.execute(
        f"SELECT {_column_sql(columns)} FROM {table} ORDER BY {_column_sql(PRIMARY_KEYS[table])}"
    ).fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _map_import_users(
    destination: Connection,
    rows: Sequence[dict[str, object]],
    id_factory: Callable[[], str],
) -> tuple[dict[str, str], list[dict[str, object]]]:
    identifier_map: dict[str, str] = {}
    to_insert: list[dict[str, object]] = []
    for original in rows:
        source_id = str(original["user_id"])
        existing_email = destination.execute(
            "SELECT user_id FROM users WHERE email = ? COLLATE NOCASE", (original["email"],)
        ).fetchone()
        if existing_email is not None:
            identifier_map[source_id] = str(existing_email[0])
            continue
        destination_id = source_id
        if (
            destination.execute("SELECT 1 FROM users WHERE user_id = ?", (source_id,)).fetchone()
            is not None
        ):
            destination_id = id_factory()
        identifier_map[source_id] = destination_id
        row = dict(original)
        row["user_id"] = destination_id
        row["role"] = "viewer"
        row["is_active"] = 0
        to_insert.append(row)
    return identifier_map, to_insert


def _allocate_id_map(
    destination: Connection,
    table: str,
    id_column: str,
    rows: Sequence[dict[str, object]],
    id_factory: Callable[[], str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        source_id = str(row[id_column])
        exists = destination.execute(
            f"SELECT 1 FROM {table} WHERE {id_column} = ?", (source_id,)
        ).fetchone()
        result[source_id] = id_factory() if exists is not None else source_id
    return result


def _transform_import_rows(
    destination: Connection,
    data: dict[str, list[dict[str, object]]],
    *,
    user_map: Mapping[str, str],
    experiment_map: Mapping[str, str],
    plate_map: Mapping[str, str],
    well_map: Mapping[str, str],
    source_map: Mapping[str, str],
    revision_map: Mapping[str, str],
    result_map: Mapping[str, str],
    event_map: Mapping[str, str],
    export_id: str,
) -> dict[str, list[dict[str, object]]]:
    transformed = {table: [dict(row) for row in rows] for table, rows in data.items()}
    for row in transformed["experiments"]:
        row["experiment_id"] = experiment_map[str(row["experiment_id"])]
        row["created_by"] = user_map[str(row["created_by"])]
    for row in transformed["experiment_tags"]:
        row["experiment_id"] = experiment_map[str(row["experiment_id"])]
    for row in transformed["plates"]:
        row["plate_id"] = plate_map[str(row["plate_id"])]
        row["experiment_id"] = experiment_map[str(row["experiment_id"])]
        row["created_by"] = user_map[str(row["created_by"])]
        if row["deleted_by"] is not None:
            row["deleted_by"] = user_map[str(row["deleted_by"])]
    for row in transformed["wells"]:
        row["well_id"] = well_map[str(row["well_id"])]
        row["plate_id"] = plate_map[str(row["plate_id"])]
    for row in transformed["well_conditions"]:
        row["well_id"] = well_map[str(row["well_id"])]
    for row in transformed["import_sources"]:
        source_id = str(row["source_id"])
        row["source_id"] = source_map[source_id]
        if row["plate_id"] is not None:
            row["plate_id"] = plate_map[str(row["plate_id"])]
        row["imported_by"] = user_map[str(row["imported_by"])]
        if (
            destination.execute(
                "SELECT 1 FROM import_sources WHERE idempotency_key = ?", (row["idempotency_key"],)
            ).fetchone()
            is not None
        ):
            row["idempotency_key"] = f"portable:{export_id}:source:{source_id}"
    for table in ("growth_measurements", "mic_readings"):
        for row in transformed[table]:
            row["plate_id"] = plate_map[str(row["plate_id"])]
            row["well_id"] = well_map[str(row["well_id"])]
    for row in transformed["analysis_revisions"]:
        row["revision_id"] = revision_map[str(row["revision_id"])]
        row["plate_id"] = plate_map[str(row["plate_id"])]
        row["created_by"] = user_map[str(row["created_by"])]
        if (
            bool(row["is_current"])
            and destination.execute(
                "SELECT 1 FROM analysis_revisions "
                "WHERE plate_id = ? AND algorithm_name = ? AND is_current = 1",
                (row["plate_id"], row["algorithm_name"]),
            ).fetchone()
            is not None
        ):
            row["is_current"] = 0
    for table in ("growth_backgrounds", "growth_metrics", "mic_well_calls"):
        for row in transformed[table]:
            row["revision_id"] = revision_map[str(row["revision_id"])]
            if "well_id" in row:
                row["well_id"] = well_map[str(row["well_id"])]
    for row in transformed["mic_results"]:
        row["result_id"] = result_map[str(row["result_id"])]
        row["revision_id"] = revision_map[str(row["revision_id"])]
    entity_maps: dict[str, Mapping[str, str]] = {
        "experiment": experiment_map,
        "plate": plate_map,
        "well": well_map,
        "revision": revision_map,
        "source": source_map,
        "mic_result": result_map,
    }
    for row in transformed["provenance_events"]:
        row["event_id"] = event_map[str(row["event_id"])]
        row["actor_id"] = user_map[str(row["actor_id"])]
        mapping = entity_maps.get(str(row["entity_type"]))
        if mapping is not None:
            row["entity_id"] = mapping.get(str(row["entity_id"]), str(row["entity_id"]))
    return transformed


def _insert_dict_rows(
    connection: Connection, table: str, rows: Sequence[dict[str, object]]
) -> None:
    columns = TABLE_COLUMNS[table]
    _insert_rows(connection, table, ([row[column] for column in columns] for row in rows))


def _validate_schema_objects(connection: sqlite3.Connection) -> None:
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'view', 'trigger') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    expected_tables = set(TABLE_COLUMNS) | {
        "schema_migrations",
        "portable_manifest",
        "portable_table_checksums",
    }
    actual_tables = {str(name) for object_type, name in objects if object_type == "table"}
    views = {str(name) for object_type, name in objects if object_type == "view"}
    triggers = {str(name) for object_type, name in objects if object_type == "trigger"}
    if actual_tables != expected_tables:
        raise PortableValidationError("Portable database has missing or unexpected tables")
    if views or triggers != TRIGGER_NAMES:
        raise PortableValidationError("Portable database has unexpected executable schema objects")


def _validate_complete_backup(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise PortableValidationError("Backup integrity check failed")
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'view', 'trigger') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {str(name) for object_type, name in objects if object_type == "table"}
    views = {str(name) for object_type, name in objects if object_type == "view"}
    triggers = {str(name) for object_type, name in objects if object_type == "trigger"}
    if tables != set(TABLE_COLUMNS) | {"schema_migrations"}:
        raise PortableValidationError("Backup has missing or unexpected tables")
    if views or triggers != TRIGGER_NAMES:
        raise PortableValidationError("Backup has unexpected executable schema objects")
    migration = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if migration != (SCHEMA_VERSION,):
        raise PortableValidationError("Backup schema version is unsupported")


def _column_values(
    connection: Connection,
    sql: str,
    parameters: Sequence[object],
    *,
    omit_null: bool = False,
) -> tuple[str, ...]:
    values = [row[0] for row in connection.execute(sql, parameters).fetchall()]
    if omit_null:
        values = [value for value in values if value is not None]
    return tuple(sorted({str(value) for value in values}))


def _in_clause(column: str, values: Sequence[object]) -> str:
    if not values:
        return "0 = 1"
    return f"{column} IN ({', '.join('?' for _ in values)})"


def _column_sql(columns: Sequence[str]) -> str:
    return ", ".join(columns)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _json_string_tuple(text: str, field: str) -> tuple[str, ...]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise PortableValidationError(f"Invalid {field}") from error
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PortableValidationError(f"Invalid {field}")
    return tuple(value)


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
