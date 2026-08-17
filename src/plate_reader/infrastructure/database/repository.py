"""Explicit SQL repository shared by pyturso and fake-cloud connections."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime

from plate_reader.application.contracts import AssayType, ExperimentId, PlateId, RevisionId
from plate_reader.application.ports.repositories import (
    ConcentrationRange,
    PlateSnapshot,
    RunSummary,
)
from plate_reader.infrastructure.database.dbapi import Connection, Cursor
from plate_reader.infrastructure.database.growth_series import (
    GrowthSeriesCodecError,
    decode_plate_growth_series,
    encode_growth_series,
)
from plate_reader.infrastructure.database.transactions import transaction


class RecordNotFoundError(LookupError):
    pass


class ConcurrencyConflictError(RuntimeError):
    pass


class InvalidRepositoryValueError(ValueError):
    pass


@dataclass(slots=True)
class _RunSummaryMetadata:
    """Mutable aggregation state used only while materializing one query result."""

    experiment_id: ExperimentId
    plate_id: PlateId
    experiment_name: str
    plate_name: str
    assay_type: AssayType
    experiment_date: str
    project: str | None
    updated_at: str
    strains: dict[str, str] = dataclass_field(default_factory=dict)
    treatments: dict[str, str] = dataclass_field(default_factory=dict)
    concentration_bounds: dict[str | None, tuple[str | None, float, float]] = dataclass_field(
        default_factory=dict
    )

    def add_condition(
        self, strain: object, treatment: object, concentration: object, unit: object
    ) -> None:
        _add_normalized_text(self.strains, strain)
        _add_normalized_text(self.treatments, treatment)
        normalized_unit = _normalized_summary_text(unit)
        normalized_concentration = _nullable_summary_concentration(concentration)
        if normalized_concentration is None:
            return
        unit_key = normalized_unit.casefold() if normalized_unit is not None else None
        existing = self.concentration_bounds.get(unit_key)
        if existing is None:
            self.concentration_bounds[unit_key] = (
                normalized_unit,
                normalized_concentration,
                normalized_concentration,
            )
            return
        display_unit, minimum, maximum = existing
        self.concentration_bounds[unit_key] = (
            _preferred_summary_text(display_unit, normalized_unit),
            min(minimum, normalized_concentration),
            max(maximum, normalized_concentration),
        )

    def as_summary(self) -> RunSummary:
        return RunSummary(
            experiment_id=self.experiment_id,
            plate_id=self.plate_id,
            experiment_name=self.experiment_name,
            plate_name=self.plate_name,
            assay_type=self.assay_type,
            experiment_date=self.experiment_date,
            project=self.project,
            updated_at=self.updated_at,
            strains=tuple(self.strains[key] for key in sorted(self.strains)),
            treatments=tuple(self.treatments[key] for key in sorted(self.treatments)),
            concentration_ranges=tuple(
                ConcentrationRange(minimum=minimum, maximum=maximum, unit=unit)
                for _unit_key, (unit, minimum, maximum) in sorted(
                    self.concentration_bounds.items(), key=_summary_unit_sort_key
                )
            ),
        )


_MIC_RESULT_FILTER_COLUMNS = {
    "experiment_date": "e.experiment_date",
    "experiment_name": "e.name",
    "project": "e.project",
    "operator_name": "e.operator_name",
    "reader": "e.reader",
    "incubation_time_hours": "e.incubation_time_hours",
    "inoculum_od": "e.inoculum_od",
    "growth_phase": "e.growth_phase",
    "harvest_od": "e.harvest_od",
    "doubling_time_minutes": "e.doubling_time_minutes",
    "experiment_notes": "e.notes",
    "plate_name": "p.plate_name",
    "plate_format": "p.plate_format",
    "threshold": "p.threshold",
    "plate_created_at": "p.created_at",
    "strain": "mr.strain",
    "treatment": "mr.treatment",
    "medium": "mr.medium",
    "replicate": "mr.replicate",
    "mic_operator": "mr.mic_operator",
    "mic_value": "mr.mic_value",
    "mic_unit": "mr.mic_unit",
    "calculation_status": "mr.calculation_status",
    "warning": "mr.warning",
}

_MAX_GROWTH_COMPARISON_PLATES = 100


class SqlPlateReaderRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def transaction(self) -> AbstractContextManager[None]:
        return transaction(self.connection)

    def upsert_user(self, values: Mapping[str, object]) -> str:
        user_id = _optional_str(values, "user_id") or _new_id()
        timestamp = _optional_str(values, "updated_at") or _now()
        email = _required_str(values, "email").casefold()
        display_name = _required_str(values, "display_name")
        role = _required_str(values, "role")
        is_active = _bool_int(values.get("is_active", True))
        existing = self.connection.execute(
            "SELECT user_id FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if existing is None:
            created_at = _optional_str(values, "created_at") or timestamp
            self.connection.execute(
                "INSERT INTO users "
                "(user_id, email, display_name, role, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, email, display_name, role, is_active, created_at, timestamp),
            )
            return user_id
        existing_id = str(existing[0])
        self.connection.execute(
            "UPDATE users SET display_name = ?, role = ?, is_active = ?, updated_at = ? "
            "WHERE user_id = ?",
            (display_name, role, is_active, timestamp, existing_id),
        )
        return existing_id

    def source_exists(self, idempotency_key: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM import_sources WHERE idempotency_key = ? AND status = 'imported'",
                (idempotency_key,),
            ).fetchone()
            is not None
        )

    def user_by_email(self, email: str) -> dict[str, object] | None:
        cursor = self.connection.execute(
            "SELECT user_id, email, display_name, role, is_active, created_at, updated_at "
            "FROM users WHERE email = ? COLLATE NOCASE",
            (email.casefold(),),
        )
        row = cursor.fetchone()
        return None if row is None else _row_dict(cursor, row)

    def plate_for_source(self, idempotency_key: str) -> PlateId | None:
        row = self.connection.execute(
            "SELECT plate_id FROM import_sources WHERE idempotency_key = ? AND status = 'imported'",
            (idempotency_key,),
        ).fetchone()
        return PlateId(str(row[0])) if row is not None and row[0] is not None else None

    def plate_for_legacy_run_id(self, legacy_run_id: str) -> PlateId | None:
        row = self.connection.execute(
            "SELECT plate_id FROM plates WHERE legacy_run_id = ?", (legacy_run_id,)
        ).fetchone()
        return PlateId(str(row[0])) if row is not None else None

    def record_import_source(self, values: Mapping[str, object]) -> str:
        source_id = _optional_str(values, "source_id") or _new_id()
        self.connection.execute(
            "INSERT INTO import_sources "
            "(source_id, plate_id, source_kind, original_filename, content_sha256, byte_size, "
            "parser_version, idempotency_key, status, imported_by, imported_at, custom_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                _nullable_str(values.get("plate_id")),
                _required_str(values, "source_kind"),
                _required_str(values, "original_filename"),
                _required_str(values, "content_sha256"),
                _required_int(values, "byte_size", minimum=0),
                _required_str(values, "parser_version"),
                _required_str(values, "idempotency_key"),
                _required_str(values, "status"),
                _required_str(values, "imported_by"),
                _optional_str(values, "imported_at") or _now(),
                _json_text(values.get("custom_json", {})),
            ),
        )
        return source_id

    def create_experiment(self, values: dict[str, object]) -> ExperimentId:
        experiment_id = ExperimentId(_optional_str(values, "experiment_id") or _new_id())
        timestamp = _optional_str(values, "created_at") or _now()
        self.connection.execute(
            "INSERT INTO experiments "
            "(experiment_id, name, project, experiment_date, operator_name, reader, "
            "incubation_time_hours, inoculum_od, growth_phase, harvest_od, "
            "doubling_time_minutes, notes, custom_json, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                _required_str(values, "name"),
                _nullable_str(values.get("project")),
                _required_str(values, "experiment_date"),
                _nullable_str(values.get("operator_name")),
                _nullable_str(values.get("reader")),
                _nullable_float(values.get("incubation_time_hours")),
                _nullable_float(values.get("inoculum_od")),
                _nullable_str(values.get("growth_phase")),
                _nullable_float(values.get("harvest_od")),
                _nullable_float(values.get("doubling_time_minutes")),
                _nullable_str(values.get("notes")),
                _json_text(values.get("custom_json", {})),
                _required_str(values, "created_by"),
                timestamp,
                _optional_str(values, "updated_at") or timestamp,
            ),
        )
        for tag in _string_sequence(values.get("tags", ())):
            self.connection.execute(
                "INSERT INTO experiment_tags(experiment_id, tag) VALUES (?, ?)",
                (experiment_id, tag),
            )
        return experiment_id

    def create_plate(self, values: dict[str, object]) -> PlateId:
        plate_id = PlateId(_optional_str(values, "plate_id") or _new_id())
        timestamp = _optional_str(values, "created_at") or _now()
        self.connection.execute(
            "INSERT INTO plates "
            "(plate_id, experiment_id, assay_type, plate_name, plate_format, "
            "lifecycle_status, instrument, channel, temperature, temperature_unit, "
            "manual_subtraction, threshold, threshold_method, background_method, "
            "is_locked, is_checked, legacy_run_id, custom_json, created_by, created_at, "
            "updated_at, deleted_at, deleted_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plate_id,
                _required_str(values, "experiment_id"),
                _required_str(values, "assay_type"),
                _required_str(values, "plate_name"),
                _int_value(values.get("plate_format", 96), minimum=1),
                _str_value(values.get("lifecycle_status", "draft")),
                _nullable_str(values.get("instrument")),
                _nullable_str(values.get("channel")),
                _nullable_float(values.get("temperature")),
                _nullable_str(values.get("temperature_unit")),
                _float_value(values.get("manual_subtraction", 0.0)),
                _nullable_float(values.get("threshold")),
                _nullable_str(values.get("threshold_method")),
                _nullable_str(values.get("background_method")),
                _bool_int(values.get("is_locked", False)),
                _bool_int(values.get("is_checked", False)),
                _nullable_str(values.get("legacy_run_id")),
                _json_text(values.get("custom_json", {})),
                _required_str(values, "created_by"),
                timestamp,
                _optional_str(values, "updated_at") or timestamp,
                _nullable_str(values.get("deleted_at")),
                _nullable_str(values.get("deleted_by")),
            ),
        )
        return plate_id

    def insert_wells(self, plate_id: PlateId, rows: Sequence[dict[str, object]]) -> None:
        timestamp = _now()
        parameters = []
        for row in rows:
            created_at = _optional_str(row, "created_at") or timestamp
            parameters.append(
                (
                    _optional_str(row, "well_id") or _new_id(),
                    plate_id,
                    _required_str(row, "position"),
                    _required_int(row, "row_index", minimum=0),
                    _required_int(row, "column_index", minimum=0),
                    _nullable_str(row.get("raw_label")),
                    _nullable_str(row.get("display_name")),
                    _bool_int(row.get("is_blank", False)),
                    _nullable_str(row.get("background_group")),
                    _bool_int(row.get("plot_selected", False)),
                    _nullable_str(row.get("notes")),
                    _json_text(row.get("custom_json", {})),
                    created_at,
                    _optional_str(row, "updated_at") or created_at,
                )
            )
        self.connection.executemany(
            "INSERT INTO wells "
            "(well_id, plate_id, position, row_index, column_index, raw_label, display_name, "
            "is_blank, background_group, plot_selected, notes, custom_json, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            parameters,
        )

    def insert_conditions(self, rows: Sequence[dict[str, object]]) -> None:
        self.connection.executemany(
            "INSERT INTO well_conditions "
            "(well_id, strain, medium, replicate, inoculum_size, inoculum_unit, "
            "grouping_label, treatment, concentration, concentration_unit, custom_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    _required_str(row, "well_id"),
                    _nullable_str(row.get("strain")),
                    _nullable_str(row.get("medium")),
                    _nullable_int(row.get("replicate"), minimum=1),
                    _nullable_float(row.get("inoculum_size")),
                    _nullable_str(row.get("inoculum_unit")),
                    _nullable_str(row.get("grouping_label")),
                    _nullable_str(row.get("treatment")),
                    _nullable_float(row.get("concentration")),
                    _nullable_str(row.get("concentration_unit")),
                    _json_text(row.get("custom_json", {})),
                )
                for row in rows
            ],
        )

    def insert_raw_observations(self, plate_id: PlateId, rows: Sequence[dict[str, object]]) -> None:
        assay = self._plate_assay(plate_id)
        if assay is AssayType.GROWTH:
            normalized = [
                {
                    "well_id": _required_str(row, "well_id"),
                    "channel": _required_str(row, "channel"),
                    "time_index": _required_int(row, "time_index", minimum=0),
                    "elapsed_microseconds": _required_int(row, "elapsed_microseconds", minimum=0),
                    "value_raw": _nullable_float(row.get("value_raw")),
                }
                for row in rows
            ]
            well_positions = {
                str(row[0]): str(row[1])
                for row in self.connection.execute(
                    "SELECT well_id, position FROM wells WHERE plate_id = ?",
                    (plate_id,),
                ).fetchall()
            }
            try:
                chunks = encode_growth_series(str(plate_id), normalized, well_positions)
            except GrowthSeriesCodecError as error:
                raise InvalidRepositoryValueError(str(error)) from error
            self.connection.executemany(
                "INSERT INTO growth_series_chunks "
                "(plate_id, channel, positions_json, timepoints_blob, values_blob, "
                "timepoint_count, position_count, encoding, content_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk["plate_id"],
                        chunk["channel"],
                        chunk["positions_json"],
                        chunk["timepoints_blob"],
                        chunk["values_blob"],
                        chunk["timepoint_count"],
                        chunk["position_count"],
                        chunk["encoding"],
                        chunk["content_sha256"],
                    )
                    for chunk in chunks
                ],
            )
            return
        self.connection.executemany(
            "INSERT INTO mic_readings(plate_id, well_id, channel, value_raw) VALUES (?, ?, ?, ?)",
            [
                (
                    plate_id,
                    _required_str(row, "well_id"),
                    _str_value(row.get("channel", "od")),
                    _nullable_float(row.get("value_raw")),
                )
                for row in rows
            ],
        )

    def update_plate_metadata(
        self, plate_id: PlateId, expected_updated_at: str, changes: dict[str, object]
    ) -> str:
        allowed = {
            "plate_name",
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
            "custom_json",
            "deleted_at",
            "deleted_by",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise InvalidRepositoryValueError(f"Unsupported plate fields: {sorted(unknown)}")
        timestamp = _now()
        assignments = [f"{column} = ?" for column in sorted(changes)]
        values = [_database_value(changes[column]) for column in sorted(changes)]
        assignments.append("updated_at = ?")
        values.extend((timestamp, plate_id, expected_updated_at))
        cursor = self.connection.execute(
            f"UPDATE plates SET {', '.join(assignments)} WHERE plate_id = ? AND updated_at = ?",
            values,
        )
        if cursor.rowcount != 1:
            if (
                self.connection.execute(
                    "SELECT 1 FROM plates WHERE plate_id = ?", (plate_id,)
                ).fetchone()
                is None
            ):
                raise RecordNotFoundError(f"Plate not found: {plate_id}")
            raise ConcurrencyConflictError(f"Plate changed since {expected_updated_at}")
        return timestamp

    def update_experiment_metadata(
        self,
        experiment_id: ExperimentId,
        expected_updated_at: str,
        changes: dict[str, object],
    ) -> str:
        allowed = {
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
        }
        unknown = set(changes) - allowed
        if unknown:
            raise InvalidRepositoryValueError(f"Unsupported experiment fields: {sorted(unknown)}")
        timestamp = _now()
        columns = sorted(changes)
        parameters = [_database_value(changes[column]) for column in columns]
        parameters.extend((timestamp, experiment_id, expected_updated_at))
        cursor = self.connection.execute(
            f"UPDATE experiments SET "
            f"{', '.join(f'{column} = ?' for column in columns)}, updated_at = ? "
            "WHERE experiment_id = ? AND updated_at = ?",
            parameters,
        )
        if cursor.rowcount != 1:
            if (
                self.connection.execute(
                    "SELECT 1 FROM experiments WHERE experiment_id = ?", (experiment_id,)
                ).fetchone()
                is None
            ):
                raise RecordNotFoundError(f"Experiment not found: {experiment_id}")
            raise ConcurrencyConflictError(f"Experiment changed since {expected_updated_at}")
        return timestamp

    def replace_experiment_tags(self, experiment_id: ExperimentId, tags: Sequence[str]) -> None:
        normalized = _string_sequence(tags)
        self.connection.execute(
            "DELETE FROM experiment_tags WHERE experiment_id = ?", (experiment_id,)
        )
        self.connection.executemany(
            "INSERT INTO experiment_tags(experiment_id, tag) VALUES (?, ?)",
            [(experiment_id, tag) for tag in normalized],
        )

    def list_plate_templates(
        self, assay_type: AssayType | None = None
    ) -> tuple[dict[str, object], ...]:
        sql = (
            "SELECT template_id, template_name, assay_type, layout_json, created_by, "
            "created_at, updated_at FROM plate_templates"
        )
        parameters: tuple[object, ...] = ()
        if assay_type is not None:
            sql += " WHERE assay_type = ?"
            parameters = (assay_type,)
        sql += " ORDER BY template_name COLLATE NOCASE, template_id"
        return _all_dicts(self.connection.execute(sql, parameters))

    def save_plate_template(self, values: dict[str, object]) -> str:
        template_id = _optional_str(values, "template_id") or _new_id()
        name = _required_str(values, "template_name")
        assay_type = AssayType(_required_str(values, "assay_type"))
        layout_json = _json_text(values.get("layout", ()))
        created_by = _required_str(values, "created_by")
        expected_updated_at = _optional_str(values, "expected_updated_at")
        conflict = self.connection.execute(
            "SELECT template_id FROM plate_templates "
            "WHERE template_name = ? COLLATE NOCASE AND template_id <> ?",
            (name, template_id),
        ).fetchone()
        if conflict is not None:
            raise InvalidRepositoryValueError(f"Template name already exists: {name}")
        existing = self.connection.execute(
            "SELECT updated_at FROM plate_templates WHERE template_id = ?", (template_id,)
        ).fetchone()
        timestamp = _now()
        if existing is None:
            if expected_updated_at is not None:
                raise RecordNotFoundError(f"Template not found: {template_id}")
            self.connection.execute(
                "INSERT INTO plate_templates "
                "(template_id, template_name, assay_type, layout_json, created_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (template_id, name, assay_type, layout_json, created_by, timestamp, timestamp),
            )
            return template_id
        if expected_updated_at is None or str(existing[0]) != expected_updated_at:
            raise ConcurrencyConflictError(f"Template changed since {expected_updated_at}")
        cursor = self.connection.execute(
            "UPDATE plate_templates SET template_name = ?, assay_type = ?, layout_json = ?, "
            "updated_at = ? WHERE template_id = ? AND updated_at = ?",
            (name, assay_type, layout_json, timestamp, template_id, expected_updated_at),
        )
        if cursor.rowcount != 1:
            raise ConcurrencyConflictError(f"Template changed since {expected_updated_at}")
        return template_id

    def delete_plate_template(self, template_id: str, expected_updated_at: str) -> None:
        cursor = self.connection.execute(
            "DELETE FROM plate_templates WHERE template_id = ? AND updated_at = ?",
            (template_id, expected_updated_at),
        )
        if cursor.rowcount == 1:
            return
        if (
            self.connection.execute(
                "SELECT 1 FROM plate_templates WHERE template_id = ?", (template_id,)
            ).fetchone()
            is None
        ):
            raise RecordNotFoundError(f"Template not found: {template_id}")
        raise ConcurrencyConflictError(f"Template changed since {expected_updated_at}")

    def list_saved_options(self, option_type: str | None = None) -> tuple[dict[str, object], ...]:
        sql = "SELECT option_type, value, created_by, created_at FROM saved_options"
        parameters: tuple[object, ...] = ()
        if option_type is not None:
            sql += " WHERE option_type = ? COLLATE NOCASE"
            parameters = (option_type,)
        sql += " ORDER BY option_type COLLATE NOCASE, value COLLATE NOCASE"
        return _all_dicts(self.connection.execute(sql, parameters))

    def save_saved_option(self, values: dict[str, object]) -> bool:
        option_type = _required_str(values, "option_type")
        value = _required_str(values, "value")
        if (
            self.connection.execute(
                "SELECT 1 FROM saved_options WHERE option_type = ? COLLATE NOCASE "
                "AND value = ? COLLATE NOCASE",
                (option_type, value),
            ).fetchone()
            is not None
        ):
            return False
        self.connection.execute(
            "INSERT INTO saved_options(option_type, value, created_by, created_at) "
            "VALUES (?, ?, ?, ?)",
            (option_type, value, _required_str(values, "created_by"), _now()),
        )
        return True

    def delete_saved_option(self, option_type: str, value: str) -> None:
        cursor = self.connection.execute(
            "DELETE FROM saved_options WHERE option_type = ? COLLATE NOCASE "
            "AND value = ? COLLATE NOCASE",
            (_str_value(option_type), _str_value(value)),
        )
        if cursor.rowcount != 1:
            raise RecordNotFoundError(f"Saved option not found: {option_type}/{value}")

    def update_well_layout(self, plate_id: PlateId, changes: Sequence[dict[str, object]]) -> None:
        well_allowed = {
            "raw_label",
            "display_name",
            "is_blank",
            "background_group",
            "plot_selected",
            "notes",
            "custom_json",
        }
        condition_allowed = {
            "strain",
            "medium",
            "replicate",
            "inoculum_size",
            "inoculum_unit",
            "grouping_label",
            "treatment",
            "concentration",
            "concentration_unit",
        }
        for change in changes:
            position = _required_str(change, "position")
            well_id_row = self.connection.execute(
                "SELECT well_id FROM wells WHERE plate_id = ? AND position = ? COLLATE NOCASE",
                (plate_id, position),
            ).fetchone()
            if well_id_row is None:
                raise RecordNotFoundError(f"Well not found: {plate_id}/{position}")
            well_id = str(well_id_row[0])
            well_changes = {key: value for key, value in change.items() if key in well_allowed}
            condition_changes = {
                key: value for key, value in change.items() if key in condition_allowed
            }
            unknown = set(change) - {"position"} - well_allowed - condition_allowed
            if unknown:
                raise InvalidRepositoryValueError(
                    f"Unsupported well fields for {position}: {sorted(unknown)}"
                )
            if well_changes:
                columns = sorted(well_changes)
                parameters = [_database_value(well_changes[column]) for column in columns]
                parameters.extend((_now(), well_id))
                self.connection.execute(
                    f"UPDATE wells SET {', '.join(f'{column} = ?' for column in columns)}, "
                    "updated_at = ? WHERE well_id = ?",
                    parameters,
                )
            if condition_changes:
                self.connection.execute(
                    "INSERT OR IGNORE INTO well_conditions(well_id) VALUES (?)", (well_id,)
                )
                columns = sorted(condition_changes)
                parameters = [_database_value(condition_changes[column]) for column in columns]
                parameters.append(well_id)
                self.connection.execute(
                    f"UPDATE well_conditions SET "
                    f"{', '.join(f'{column} = ?' for column in columns)} WHERE well_id = ?",
                    parameters,
                )

    def add_analysis_revision(self, values: dict[str, object]) -> RevisionId:
        revision_id = RevisionId(_optional_str(values, "revision_id") or _new_id())
        plate_id = _required_str(values, "plate_id")
        algorithm_name = _required_str(values, "algorithm_name")
        is_current = _bool_int(values.get("is_current", True))
        if is_current:
            self.connection.execute(
                "UPDATE analysis_revisions SET is_current = 0 "
                "WHERE plate_id = ? AND algorithm_name = ? AND is_current = 1",
                (plate_id, algorithm_name),
            )
        self.connection.execute(
            "INSERT INTO analysis_revisions "
            "(revision_id, plate_id, assay_type, algorithm_name, algorithm_version, "
            "parameters_json, input_sha256, is_current, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                plate_id,
                _required_str(values, "assay_type"),
                algorithm_name,
                _required_str(values, "algorithm_version"),
                _json_text(values.get("parameters_json", {})),
                _required_str(values, "input_sha256"),
                is_current,
                _required_str(values, "created_by"),
                _optional_str(values, "created_at") or _now(),
            ),
        )
        return revision_id

    def insert_growth_backgrounds(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None:
        self.connection.executemany(
            "INSERT INTO growth_backgrounds "
            "(revision_id, background_group, channel, time_index, elapsed_microseconds, "
            "mean_value, std_value, coefficient_of_variation, blank_count, qc_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    revision_id,
                    _required_str(row, "background_group"),
                    _required_str(row, "channel"),
                    _required_int(row, "time_index", minimum=0),
                    _required_int(row, "elapsed_microseconds", minimum=0),
                    _float_value(row.get("mean_value")),
                    _nullable_float(row.get("std_value")),
                    _nullable_float(row.get("coefficient_of_variation")),
                    _required_int(row, "blank_count", minimum=1),
                    _required_str(row, "qc_status"),
                )
                for row in rows
            ],
        )

    def insert_growth_metrics(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None:
        self.connection.executemany(
            "INSERT INTO growth_metrics "
            "(revision_id, well_id, channel, metric_name, metric_value, metric_unit, "
            "quality_flag) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    revision_id,
                    _required_str(row, "well_id"),
                    _required_str(row, "channel"),
                    _required_str(row, "metric_name"),
                    _nullable_float(row.get("metric_value")),
                    _nullable_str(row.get("metric_unit")),
                    _nullable_str(row.get("quality_flag")),
                )
                for row in rows
            ],
        )

    def insert_mic_well_calls(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None:
        self.connection.executemany(
            "INSERT INTO mic_well_calls "
            "(revision_id, well_id, background_value, value_background_subtracted, growth_call) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    revision_id,
                    _required_str(row, "well_id"),
                    _float_value(row.get("background_value")),
                    _float_value(row.get("value_background_subtracted")),
                    _nullable_bool_int(row.get("growth_call")),
                )
                for row in rows
            ],
        )

    def insert_mic_results(
        self, revision_id: RevisionId, rows: Sequence[dict[str, object]]
    ) -> None:
        self.connection.executemany(
            "INSERT INTO mic_results "
            "(result_id, revision_id, group_key, strain, treatment, medium, replicate, "
            "mic_value, mic_operator, mic_unit, threshold_used, "
            "lowest_tested_concentration, highest_tested_concentration, concentrations_json, "
            "point_count, calculation_status, warning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    _optional_str(row, "result_id") or _new_id(),
                    revision_id,
                    _required_str(row, "group_key"),
                    _required_str(row, "strain"),
                    _required_str(row, "treatment"),
                    _required_str(row, "medium"),
                    _required_int(row, "replicate", minimum=1),
                    _float_value(row.get("mic_value")),
                    _required_str(row, "mic_operator"),
                    _required_str(row, "mic_unit"),
                    _float_value(row.get("threshold_used")),
                    _float_value(row.get("lowest_tested_concentration")),
                    _float_value(row.get("highest_tested_concentration")),
                    _json_text(row.get("concentrations_json", ())),
                    _required_int(row, "point_count", minimum=1),
                    _str_value(row.get("calculation_status", "success")),
                    _nullable_str(row.get("warning")),
                )
                for row in rows
            ],
        )

    def append_provenance(self, values: Mapping[str, object]) -> str:
        event_id = _optional_str(values, "event_id") or _new_id()
        self.connection.execute(
            "INSERT INTO provenance_events "
            "(event_id, actor_id, event_type, entity_type, entity_id, occurred_at, "
            "details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                _required_str(values, "actor_id"),
                _required_str(values, "event_type"),
                _required_str(values, "entity_type"),
                _required_str(values, "entity_id"),
                _optional_str(values, "occurred_at") or _now(),
                _json_text(values.get("details_json", {})),
            ),
        )
        return event_id

    def search_runs(self, filters: dict[str, object]) -> Sequence[RunSummary]:
        where = ["p.deleted_at IS NULL"]
        parameters: list[object] = []
        text = _optional_str(filters, "text")
        if text:
            where.append(
                "(e.name LIKE ? OR p.plate_name LIKE ? OR e.project LIKE ? OR EXISTS ("
                "SELECT 1 FROM wells tw JOIN well_conditions twc ON twc.well_id = tw.well_id "
                "WHERE tw.plate_id = p.plate_id AND tw.is_blank = 0 AND "
                "(twc.strain LIKE ? OR twc.treatment LIKE ? OR twc.medium LIKE ?)))"
            )
            pattern = f"%{text}%"
            parameters.extend((pattern, pattern, pattern, pattern, pattern, pattern))
        for key, column in (
            ("assay_type", "p.assay_type"),
            ("project", "e.project"),
        ):
            value = _optional_str(filters, key)
            if value:
                where.append(f"{column} = ?")
                parameters.append(value)
        for key, operator in (("date_from", ">="), ("date_to", "<=")):
            value = _optional_str(filters, key)
            if value:
                where.append(f"e.experiment_date {operator} ?")
                parameters.append(value)
        condition_filters = {
            key: _optional_str(filters, key) for key in ("strain", "medium", "treatment")
        }
        active_condition_filters = tuple(
            (key, value) for key, value in condition_filters.items() if value
        )
        if active_condition_filters:
            predicates = ["fw.plate_id = p.plate_id", "fw.is_blank = 0"]
            for key, value in active_condition_filters:
                predicates.append(f"fwc.{key} = ?")
                parameters.append(value)
            where.append(
                "EXISTS (SELECT 1 FROM wells fw "
                "JOIN well_conditions fwc ON fwc.well_id = fw.well_id WHERE "
                f"{' AND '.join(predicates)})"
            )
        if not bool(filters.get("include_deleted", False)):
            pass
        else:
            where.remove("p.deleted_at IS NULL")
        limit = _int_value(filters.get("limit", 100), minimum=1, maximum=500)
        offset = _int_value(filters.get("offset", 0), minimum=0)
        parameters.extend((limit, offset))
        cursor = self.connection.execute(
            "WITH candidate_runs AS ("
            "SELECT e.experiment_id, p.plate_id, e.name AS experiment_name, "
            "p.plate_name, p.assay_type, e.experiment_date, e.project, p.updated_at "
            "FROM plates p JOIN experiments e ON e.experiment_id = p.experiment_id "
            f"WHERE {' AND '.join(where) if where else '1 = 1'} "
            "ORDER BY p.updated_at DESC, p.plate_id ASC LIMIT ? OFFSET ?"
            ") "
            "SELECT cr.experiment_id, cr.plate_id, cr.experiment_name, cr.plate_name, "
            "cr.assay_type, cr.experiment_date, cr.project, cr.updated_at, "
            "wc.strain, wc.treatment, wc.concentration, wc.concentration_unit "
            "FROM candidate_runs cr "
            "LEFT JOIN wells w ON w.plate_id = cr.plate_id AND w.is_blank = 0 "
            "LEFT JOIN well_conditions wc ON wc.well_id = w.well_id "
            "ORDER BY cr.updated_at DESC, cr.plate_id ASC, w.row_index ASC, w.column_index ASC",
            parameters,
        )
        summaries: dict[str, _RunSummaryMetadata] = {}
        for row in cursor.fetchall():
            plate_id = str(row[1])
            metadata = summaries.get(plate_id)
            if metadata is None:
                metadata = _RunSummaryMetadata(
                    experiment_id=ExperimentId(str(row[0])),
                    plate_id=PlateId(plate_id),
                    experiment_name=str(row[2]),
                    plate_name=str(row[3]),
                    assay_type=AssayType(str(row[4])),
                    experiment_date=str(row[5]),
                    project=None if row[6] is None else str(row[6]),
                    updated_at=str(row[7]),
                )
                summaries[plate_id] = metadata
            metadata.add_condition(row[8], row[9], row[10], row[11])
        return tuple(metadata.as_summary() for metadata in summaries.values())

    def growth_comparison_wells(
        self, plate_ids: Sequence[PlateId]
    ) -> tuple[dict[str, object], ...]:
        """Load selected Growth well conditions without reading observations.

        Missing, deleted, and non-Growth IDs intentionally produce no rows. The
        application service owns reporting which requested IDs were unavailable.
        """

        requested_ids = _validated_growth_comparison_plate_ids(plate_ids)
        placeholders = ", ".join("?" for _plate_id in requested_ids)
        order_by = " ".join(f"WHEN ? THEN {index}" for index, _plate_id in enumerate(requested_ids))
        cursor = self.connection.execute(
            "SELECT p.plate_id, e.name AS experiment_name, p.plate_name, w.well_id, "
            "w.position, w.display_name, wc.strain, wc.treatment, wc.concentration, "
            "wc.concentration_unit, wc.medium, wc.replicate, wc.grouping_label, "
            "wc.inoculum_size, wc.inoculum_unit, w.is_blank "
            "FROM plates p JOIN experiments e ON e.experiment_id = p.experiment_id "
            "JOIN wells w ON w.plate_id = p.plate_id "
            "LEFT JOIN well_conditions wc ON wc.well_id = w.well_id "
            f"WHERE p.plate_id IN ({placeholders}) AND p.assay_type = ? AND p.deleted_at IS NULL "
            f"ORDER BY CASE p.plate_id {order_by} END, w.row_index ASC, w.column_index ASC, "
            "w.well_id ASC",
            (*requested_ids, AssayType.GROWTH, *requested_ids),
        )
        return _all_dicts(cursor)

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None:
        metadata_cursor = self.connection.execute(
            "SELECT e.*, p.*, e.updated_at AS experiment_updated_at, "
            "e.custom_json AS experiment_custom_json, p.custom_json AS plate_custom_json "
            "FROM plates p JOIN experiments e "
            "ON e.experiment_id = p.experiment_id WHERE p.plate_id = ?",
            (plate_id,),
        )
        metadata_row = metadata_cursor.fetchone()
        if metadata_row is None:
            return None
        metadata = _row_dict(metadata_cursor, metadata_row)
        metadata["tags"] = tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT tag FROM experiment_tags WHERE experiment_id = ? "
                "ORDER BY tag COLLATE NOCASE",
                (metadata["experiment_id"],),
            ).fetchall()
        )
        wells = _all_dicts(
            self.connection.execute(
                "SELECT w.*, wc.strain, wc.medium, wc.replicate, wc.inoculum_size, "
                "wc.inoculum_unit, wc.grouping_label, wc.treatment, wc.concentration, "
                "wc.concentration_unit, wc.custom_json AS condition_custom_json "
                "FROM wells w LEFT JOIN well_conditions wc ON wc.well_id = w.well_id "
                "WHERE w.plate_id = ? ORDER BY w.row_index, w.column_index",
                (plate_id,),
            )
        )
        assay = AssayType(str(metadata["assay_type"]))
        if assay is AssayType.GROWTH:
            raw = self._load_growth_measurements(plate_id)
        else:
            raw = _all_dicts(
                self.connection.execute(
                    "SELECT * FROM mic_readings WHERE plate_id = ?", (plate_id,)
                )
            )
        revisions = _all_dicts(
            self.connection.execute(
                "SELECT * FROM analysis_revisions WHERE plate_id = ? ORDER BY created_at",
                (plate_id,),
            )
        )
        return PlateSnapshot(
            plate_id=plate_id,
            metadata=metadata,
            wells=wells,
            raw_observations=raw,
            revisions=revisions,
        )

    def plate_cache_token(self, plate_id: PlateId) -> str | None:
        row = self.connection.execute(
            "SELECT p.updated_at, COALESCE(MAX(ar.created_at), ''), "
            "COALESCE(MAX(CASE WHEN ar.is_current = 1 THEN ar.revision_id END), '') "
            "FROM plates p LEFT JOIN analysis_revisions ar ON ar.plate_id = p.plate_id "
            "WHERE p.plate_id = ? GROUP BY p.plate_id, p.updated_at",
            (plate_id,),
        ).fetchone()
        if row is None:
            return None
        return hashlib.sha256("\0".join(str(value) for value in row).encode()).hexdigest()

    def growth_backgrounds(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]:
        return _all_dicts(
            self.connection.execute(
                "SELECT revision_id, background_group, channel, time_index, "
                "elapsed_microseconds, mean_value, std_value, coefficient_of_variation, "
                "blank_count, qc_status FROM growth_backgrounds WHERE revision_id = ? "
                "ORDER BY background_group, channel, time_index",
                (revision_id,),
            )
        )

    def mic_well_calls(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]:
        return _all_dicts(
            self.connection.execute(
                "SELECT revision_id, well_id, background_value, "
                "value_background_subtracted, growth_call FROM mic_well_calls "
                "WHERE revision_id = ? ORDER BY well_id",
                (revision_id,),
            )
        )

    def mic_results(self, revision_id: RevisionId) -> tuple[dict[str, object], ...]:
        return _all_dicts(
            self.connection.execute(
                "SELECT result_id, revision_id, group_key, strain, treatment, medium, "
                "replicate, mic_value, mic_operator, mic_unit, threshold_used, "
                "lowest_tested_concentration, highest_tested_concentration, "
                "concentrations_json, point_count, calculation_status, warning "
                "FROM mic_results WHERE revision_id = ? ORDER BY strain, treatment, medium, "
                "replicate, group_key",
                (revision_id,),
            )
        )

    def search_mic_results(self, filters: Mapping[str, object]) -> tuple[dict[str, object], ...]:
        where = ["ar.is_current = 1"]
        parameters: list[object] = []
        if not bool(filters.get("include_deleted", False)):
            where.append("p.deleted_at IS NULL")
        for key in ("strain", "treatment", "medium"):
            value = _optional_str(filters, key)
            if value:
                where.append(f"mr.{key} = ?")
                parameters.append(value)
        for key in ("strains", "treatments"):
            values = _filter_string_sequence(filters.get(key, ()))
            if values:
                column = "strain" if key == "strains" else "treatment"
                where.append(f"mr.{column} IN ({','.join('?' for _value in values)})")
                parameters.extend(values)
        text = _optional_str(filters, "text")
        if text:
            pattern = f"%{text}%"
            where.append(
                "(e.name LIKE ? OR p.plate_name LIKE ? OR mr.strain LIKE ? OR "
                "mr.treatment LIKE ? OR mr.medium LIKE ?)"
            )
            parameters.extend((pattern, pattern, pattern, pattern, pattern))
        for field, value in _field_filter_sequence(filters.get("field_filters", ())):
            pattern = f"%{value}%"
            if field == "tags":
                where.append(
                    "EXISTS (SELECT 1 FROM experiment_tags et "
                    "WHERE et.experiment_id = e.experiment_id AND et.tag LIKE ?)"
                )
                parameters.append(pattern)
            else:
                mapped_column = _MIC_RESULT_FILTER_COLUMNS.get(field)
                if mapped_column is not None:
                    where.append(f"CAST({mapped_column} AS TEXT) LIKE ?")
                    parameters.append(pattern)
                    continue
                if not field.startswith("custom."):
                    raise InvalidRepositoryValueError(f"Unknown MIC result filter field: {field}")
                where.append(
                    "EXISTS (SELECT 1 FROM wells sw "
                    "JOIN well_conditions swc ON swc.well_id = sw.well_id "
                    "JOIN json_each(sw.custom_json) sj "
                    "WHERE sw.plate_id = p.plate_id AND "
                    "COALESCE(NULLIF(TRIM(swc.strain), ''), 'Unknown') = mr.strain AND "
                    "COALESCE(NULLIF(TRIM(swc.treatment), ''), 'Unknown') = mr.treatment AND "
                    "COALESCE(NULLIF(TRIM(swc.medium), ''), 'Unknown') = mr.medium AND "
                    "swc.replicate = mr.replicate AND sj.key = ? "
                    "AND CAST(sj.value AS TEXT) LIKE ?)"
                )
                parameters.extend((field.removeprefix("custom."), pattern))
        limit = _int_value(filters.get("limit", 100), minimum=1, maximum=500)
        offset = _int_value(filters.get("offset", 0), minimum=0)
        parameters.extend((limit, offset))
        rows = _all_dicts(
            self.connection.execute(
                "SELECT mr.*, p.plate_id, p.plate_name, p.is_locked, p.is_checked, "
                "p.deleted_at, p.plate_format, p.threshold, p.created_at AS plate_created_at, "
                "e.experiment_id, e.name AS experiment_name, e.experiment_date, e.project, "
                "e.operator_name, e.reader, e.incubation_time_hours, e.inoculum_od, "
                "e.growth_phase, e.harvest_od, e.doubling_time_minutes, "
                "e.notes AS experiment_notes, "
                "(SELECT group_concat(tag, ', ') FROM "
                "(SELECT tag FROM experiment_tags et WHERE et.experiment_id = e.experiment_id "
                "ORDER BY tag COLLATE NOCASE)) AS tags FROM mic_results mr "
                "JOIN analysis_revisions ar ON ar.revision_id = mr.revision_id "
                "JOIN plates p ON p.plate_id = ar.plate_id "
                "JOIN experiments e ON e.experiment_id = p.experiment_id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY e.experiment_date DESC, p.updated_at DESC, mr.strain, "
                "mr.treatment, mr.replicate LIMIT ? OFFSET ?",
                parameters,
            )
        )
        return self._add_mic_result_custom_values(rows)

    def mic_result_search_catalog(self) -> Mapping[str, object]:
        base = (
            " FROM mic_results mr "
            "JOIN analysis_revisions ar ON ar.revision_id = mr.revision_id "
            "JOIN plates p ON p.plate_id = ar.plate_id "
            "WHERE ar.is_current = 1 AND p.deleted_at IS NULL"
        )
        strains = tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT mr.strain" + base + " ORDER BY mr.strain COLLATE NOCASE"
            ).fetchall()
            if row[0]
        )
        treatments = tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT mr.treatment" + base + " ORDER BY mr.treatment COLLATE NOCASE"
            ).fetchall()
            if row[0]
        )
        custom_fields: set[str] = set()
        for row in self.connection.execute(
            "SELECT w.custom_json FROM wells w JOIN plates p ON p.plate_id = w.plate_id "
            "WHERE p.assay_type = 'mic' AND p.deleted_at IS NULL"
        ).fetchall():
            custom_fields.update(_json_object_keys(row[0]))
        return {
            "strains": strains,
            "treatments": treatments,
            "custom_fields": tuple(sorted(custom_fields, key=str.casefold)),
        }

    def _add_mic_result_custom_values(
        self, rows: tuple[dict[str, object], ...]
    ) -> tuple[dict[str, object], ...]:
        plate_ids = tuple(sorted({str(row["plate_id"]) for row in rows}))
        if not plate_ids:
            return rows
        placeholders = ",".join("?" for _plate_id in plate_ids)
        custom_by_group: dict[tuple[str, str, str, str, int], dict[str, object]] = {}
        cursor = self.connection.execute(
            "SELECT w.plate_id, wc.strain, wc.treatment, wc.medium, wc.replicate, "
            "w.custom_json FROM wells w JOIN well_conditions wc ON wc.well_id = w.well_id "
            f"WHERE w.plate_id IN ({placeholders}) ORDER BY w.row_index, w.column_index",
            plate_ids,
        )
        for plate_id, strain, treatment, medium, replicate, custom_json in cursor.fetchall():
            if replicate is None:
                continue
            group = (
                str(plate_id),
                _normalized_mic_group_value(strain),
                _normalized_mic_group_value(treatment),
                _normalized_mic_group_value(medium),
                int(replicate),
            )
            values = custom_by_group.setdefault(group, {})
            for key, value in _json_object_items(custom_json):
                values.setdefault(f"custom.{key}", value)
        enriched = []
        for source in rows:
            row = dict(source)
            group = (
                str(row["plate_id"]),
                str(row["strain"]),
                str(row["treatment"]),
                str(row["medium"]),
                _int_value(row["replicate"], minimum=1),
            )
            row.update(custom_by_group.get(group, {}))
            enriched.append(row)
        return tuple(enriched)

    def provenance_for_plate(self, plate_id: PlateId) -> tuple[dict[str, object], ...]:
        return _all_dicts(
            self.connection.execute(
                "SELECT event_id, actor_id, event_type, entity_type, entity_id, occurred_at, "
                "details_json FROM provenance_events WHERE "
                "(entity_type = 'plate' AND entity_id = ?) OR "
                "(entity_type = 'revision' AND entity_id IN "
                "(SELECT revision_id FROM analysis_revisions WHERE plate_id = ?)) "
                "ORDER BY occurred_at, event_id",
                (plate_id, plate_id),
            )
        )

    def stream_growth_measurements(
        self, plate_id: PlateId, *, chunk_size: int = 5_000
    ) -> Iterator[tuple[dict[str, object], ...]]:
        if chunk_size < 1:
            raise InvalidRepositoryValueError("chunk_size must be positive")
        rows = self._load_growth_measurements(plate_id)
        for offset in range(0, len(rows), chunk_size):
            yield rows[offset : offset + chunk_size]

    def _load_growth_measurements(self, plate_id: PlateId) -> tuple[dict[str, object], ...]:
        compact: tuple[dict[str, object], ...] = ()
        if self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'growth_series_chunks'"
        ).fetchone():
            compact_cursor = self.connection.execute(
                "SELECT plate_id, channel, positions_json, timepoints_blob, values_blob, "
                "timepoint_count, position_count, encoding, content_sha256 "
                "FROM growth_series_chunks WHERE plate_id = ? ORDER BY channel",
                (plate_id,),
            )
            compact = _all_dicts(compact_cursor)
        legacy_cursor = self.connection.execute(
            "SELECT plate_id, well_id, channel, time_index, elapsed_microseconds, value_raw "
            "FROM growth_measurements WHERE plate_id = ? "
            "ORDER BY channel, time_index, well_id",
            (plate_id,),
        )
        legacy = _all_dicts(legacy_cursor)
        if compact and legacy:
            raise RuntimeError(f"Plate has both compact and legacy growth data: {plate_id}")
        if not compact:
            return legacy
        position_well_ids = {
            str(row[0]): str(row[1])
            for row in self.connection.execute(
                "SELECT position, well_id FROM wells WHERE plate_id = ?",
                (plate_id,),
            ).fetchall()
        }
        try:
            return decode_plate_growth_series(compact, position_well_ids)
        except GrowthSeriesCodecError as error:
            raise RuntimeError(
                f"Stored growth data is invalid for plate {plate_id}: {error}"
            ) from error

    def _plate_assay(self, plate_id: PlateId) -> AssayType:
        row = self.connection.execute(
            "SELECT assay_type FROM plates WHERE plate_id = ?", (plate_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Plate not found: {plate_id}")
        return AssayType(str(row[0]))


def _row_dict(cursor: Cursor, row: Sequence[object]) -> dict[str, object]:
    if cursor.description is None:
        raise RuntimeError("Cursor does not describe result columns")
    return {str(description[0]): row[index] for index, description in enumerate(cursor.description)}


def _all_dicts(cursor: Cursor) -> tuple[dict[str, object], ...]:
    return tuple(_row_dict(cursor, row) for row in cursor.fetchall())


def _required_str(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRepositoryValueError(f"{key} must be a nonempty string")
    return value.strip()


def _optional_str(values: Mapping[str, object], key: str) -> str | None:
    return _nullable_str(values.get(key))


def _nullable_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRepositoryValueError("Expected a string or null")
    return value


def _str_value(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRepositoryValueError("Expected a nonempty string")
    return value.strip()


def _required_int(values: Mapping[str, object], key: str, *, minimum: int | None = None) -> int:
    if key not in values:
        raise InvalidRepositoryValueError(f"Missing integer: {key}")
    return _int_value(values[key], minimum=minimum)


def _int_value(value: object, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRepositoryValueError("Expected an integer")
    if minimum is not None and value < minimum:
        raise InvalidRepositoryValueError(f"Integer must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise InvalidRepositoryValueError(f"Integer must be at most {maximum}")
    return value


def _nullable_int(value: object, *, minimum: int | None = None) -> int | None:
    return None if value is None else _int_value(value, minimum=minimum)


def _float_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRepositoryValueError("Expected a number")
    result = float(value)
    if not math.isfinite(result):
        raise InvalidRepositoryValueError("Number must be finite")
    return result


def _nullable_float(value: object) -> float | None:
    return None if value is None else _float_value(value)


def _bool_int(value: object) -> int:
    if not isinstance(value, bool | int) or value not in (0, 1, False, True):
        raise InvalidRepositoryValueError("Expected a boolean")
    return int(value)


def _nullable_bool_int(value: object) -> int | None:
    return None if value is None else _bool_int(value)


def _json_text(value: object) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise InvalidRepositoryValueError("Invalid JSON text") from error
    else:
        parsed = value
    try:
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise InvalidRepositoryValueError("Value is not JSON serializable") from error


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise InvalidRepositoryValueError("Expected a sequence of strings")
    result = tuple(_str_value(item) for item in value)
    if len(result) != len(set(item.casefold() for item in result)):
        raise InvalidRepositoryValueError("Tags must be unique")
    return result


def _filter_string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise InvalidRepositoryValueError("Expected a sequence of filter strings")
    return tuple(_str_value(item) for item in value)


def _field_filter_sequence(value: object) -> tuple[tuple[str, str], ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise InvalidRepositoryValueError("Expected a sequence of MIC field filters")
    result: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, str) or not isinstance(item, Sequence) or len(item) != 2:
            raise InvalidRepositoryValueError(
                "Each MIC field filter must contain a field and value"
            )
        field, field_value = item
        result.append((_str_value(field), _str_value(field_value)))
    return tuple(result)


def _json_object_items(value: object) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, dict):
        return ()
    return tuple((str(key), item) for key, item in parsed.items() if str(key).strip())


def _json_object_keys(value: object) -> tuple[str, ...]:
    return tuple(key for key, _item in _json_object_items(value))


def _normalized_mic_group_value(value: object) -> str:
    normalized = str(value).strip() if value is not None else ""
    return normalized or "Unknown"


def _validated_growth_comparison_plate_ids(plate_ids: Sequence[PlateId]) -> tuple[PlateId, ...]:
    requested = tuple(plate_ids)
    if len(requested) < 2:
        raise InvalidRepositoryValueError("Growth comparison requires at least two plate IDs")
    if len(requested) > _MAX_GROWTH_COMPARISON_PLATES:
        raise InvalidRepositoryValueError(
            f"Growth comparison supports at most {_MAX_GROWTH_COMPARISON_PLATES} plate IDs"
        )
    normalized: list[PlateId] = []
    for plate_id in requested:
        if not isinstance(plate_id, str) or not plate_id.strip():
            raise InvalidRepositoryValueError(
                "Growth comparison plate IDs must be nonempty strings"
            )
        normalized.append(PlateId(plate_id.strip()))
    if len(set(normalized)) != len(normalized):
        raise InvalidRepositoryValueError("Growth comparison plate IDs must be unique")
    return tuple(normalized)


def _normalized_summary_text(value: object) -> str | None:
    """Return nonempty, trimmed condition metadata suitable for Library display."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _add_normalized_text(values: dict[str, str], value: object) -> None:
    normalized = _normalized_summary_text(value)
    if normalized is None:
        return
    key = normalized.casefold()
    previous = values.get(key)
    if previous is None or normalized < previous:
        values[key] = normalized


def _preferred_summary_text(current: str | None, candidate: str | None) -> str | None:
    if current is None or candidate is None:
        return current or candidate
    return min(current, candidate)


def _summary_unit_sort_key(
    item: tuple[str | None, tuple[str | None, float, float]],
) -> tuple[bool, str]:
    unit_key, _bounds = item
    return (unit_key is None, unit_key or "")


def _nullable_summary_concentration(value: object) -> float | None:
    if value is None:
        return None
    try:
        return _float_value(value)
    except InvalidRepositoryValueError:
        return None


def _database_value(value: object) -> object:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list, tuple)):
        return _json_text(value)
    return value


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()
