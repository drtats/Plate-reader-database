"""Multi-run Growth CSV export matching the laboratory analysis contract."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from plate_reader.application.contracts import Actor, AssayType, PlateId
from plate_reader.application.services.growth_workflow import (
    GrowthRunView,
    GrowthWorkflowRepository,
    LoadGrowthRunService,
)
from plate_reader.application.services.layout_columns import ListLayoutColumnsService

_LEGACY_GROWTH_MEASUREMENT_HEADERS = (
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
    "Well",
    "Time Min",
    "Signal Type",
    "Raw OD",
    "Blank",
    "BG Group",
    "Strain",
    "Media",
    "Replicate",
    "Notes",
)

# Canonical layout fields that are absent from, or only indirectly represented in,
# the legacy-compatible block. Together the two tuples expose every fixed Growth
# layout column without changing the established first 29 columns.
GROWTH_ADDITIONAL_LAYOUT_HEADERS = (
    "Raw label",
    "Display name",
    "Background group",
    "Plot",
    "Group",
    "Inoculum size",
    "Inoculum unit",
    "Treatment",
    "Concentration",
    "Concentration unit",
    "T0 added (min)",
)

GROWTH_MEASUREMENT_HEADERS = (
    *_LEGACY_GROWTH_MEASUREMENT_HEADERS,
    *GROWTH_ADDITIONAL_LAYOUT_HEADERS,
)

GROWTH_METADATA_HEADERS = (
    "Run ID",
    "Project",
    "Experiment Name",
    "Experiment Date",
    "User",
    "Instrument",
    "Temperature",
    "Source Folder",
    "Editable Metadata JSON",
    "Source Metadata JSON",
)

_EDITABLE_METADATA_KEYS = (
    "editable_metadata_json",
    "metadata_json_editable",
    "metadata_editable_json",
    "editable_metadata",
    "metadata_editable",
)
_SOURCE_METADATA_KEYS = (
    "source_metadata_json",
    "metadata_json_source",
    "metadata_source_json",
    "source_metadata",
    "metadata_source",
)
_CORRECTED_OD_FLOOR = 0.0001
_STRUCTURED_CUSTOM_KEYS = {
    "t0_added_min",
    *(f"{prefix}_{index}" for prefix in ("treatment", "conc", "unit") for index in range(1, 4)),
}


@dataclass(frozen=True, slots=True)
class ExportGrowthTabularData:
    actor: Actor
    plate_ids: tuple[PlateId, ...]


@dataclass(frozen=True, slots=True)
class GrowthTabularCsvArtifact:
    filename: str
    content: bytes
    row_count: int


@dataclass(frozen=True, slots=True)
class GrowthTabularExportBundle:
    measurements: GrowthTabularCsvArtifact
    metadata: GrowthTabularCsvArtifact
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RunContext:
    view: GrowthRunView
    run_id: str
    project: str
    experiment_name: str
    experiment_date: str
    user: str
    instrument: str
    temperature: object | None
    source_folder: str
    editable_metadata: Mapping[str, object]
    source_metadata: Mapping[str, object]
    start_datetime: datetime | None
    culture_age_hours: float
    culture_volume_ul: object | None
    microplate_id: str


class ExportGrowthTabularDataService:
    """Load selected Growth runs and create the two read-only CSV artifacts."""

    def __init__(self, repository: GrowthWorkflowRepository) -> None:
        self.repository = repository

    def execute(self, command: ExportGrowthTabularData) -> GrowthTabularExportBundle:
        if not command.plate_ids:
            raise ValueError("Select at least one Growth run to export")
        if len(set(command.plate_ids)) != len(command.plate_ids):
            raise ValueError("Growth tabular export run IDs must be unique")
        loader = LoadGrowthRunService(self.repository)
        views = tuple(loader.execute(command.actor, plate_id) for plate_id in command.plate_ids)
        custom_columns = tuple(
            column.name
            for column in ListLayoutColumnsService(self.repository).execute(
                command.actor, AssayType.GROWTH
            )
        )
        return export_growth_tabular_data(views, custom_columns=custom_columns)


def export_growth_tabular_data(
    views: Sequence[GrowthRunView],
    *,
    custom_columns: Sequence[str] = (),
) -> GrowthTabularExportBundle:
    """Build deterministic multi-run measurement and metadata CSV files."""

    if not views:
        raise ValueError("Growth tabular export requires at least one run")
    plate_ids = tuple(str(view.snapshot.plate_id) for view in views)
    if len(set(plate_ids)) != len(plate_ids):
        raise ValueError("Growth tabular export views must have unique plate IDs")

    contexts = tuple(_run_context(view) for view in views)
    exported_custom_columns = _custom_column_names(views, custom_columns)
    single_run_name = _single_run_filename_stem(contexts)

    measurement_stream = io.StringIO(newline="")
    metadata_stream = io.StringIO(newline="")
    measurement_writer = csv.writer(measurement_stream, lineterminator="\n")
    metadata_writer = csv.writer(metadata_stream, lineterminator="\n")
    measurement_writer.writerow((*GROWTH_MEASUREMENT_HEADERS, *exported_custom_columns))
    metadata_writer.writerow(GROWTH_METADATA_HEADERS)

    measurement_count = 0
    metadata_count = 0
    warnings: list[str] = []
    for context in contexts:
        warnings.extend(_run_warnings(context))
        metadata_writer.writerow(_run_metadata_row(context))
        metadata_count += 1
        for row in _measurement_rows(context, exported_custom_columns):
            measurement_writer.writerow(row)
            measurement_count += 1

    return GrowthTabularExportBundle(
        measurements=GrowthTabularCsvArtifact(
            f"{single_run_name}.csv" if single_run_name else "growth_runs.csv",
            measurement_stream.getvalue().encode("utf-8"),
            measurement_count,
        ),
        metadata=GrowthTabularCsvArtifact(
            (f"{single_run_name}_metadata.csv" if single_run_name else "growth_runs_metadata.csv"),
            metadata_stream.getvalue().encode("utf-8"),
            metadata_count,
        ),
        warnings=tuple(warnings),
    )


def _single_run_filename_stem(contexts: Sequence[_RunContext]) -> str:
    """Return an example-compatible, stable stem for a one-run export."""

    if len(contexts) != 1:
        return ""
    context = contexts[0]
    experiment = _safe_filename_component(context.experiment_name) or "growth_run"
    return f"{experiment}_{_short_run_hash(context.run_id)}"


def _safe_filename_component(value: str) -> str:
    """Normalize a user-entered experiment name without allowing path syntax."""

    normalized = "".join(
        character if character.isalnum() else "_" for character in value.casefold()
    )
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")[:160].rstrip("_")


def _short_run_hash(run_id: str) -> str:
    """Preserve hex run IDs like legacy exports; hash other stable identities."""

    compact = run_id.replace("-", "").casefold()
    if len(compact) >= 8 and all(character in "0123456789abcdef" for character in compact):
        return compact[:8]
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8]


def _run_context(view: GrowthRunView) -> _RunContext:
    metadata = view.snapshot.metadata
    if str(metadata.get("assay_type", "")) != AssayType.GROWTH:
        raise ValueError(f"Plate is not a growth run: {view.snapshot.plate_id}")
    experiment_custom = _json_object(metadata.get("experiment_custom_json"))
    plate_custom = _json_object(metadata.get("plate_custom_json"))
    legacy = _json_object(plate_custom.get("legacy_plate_meta")) or _json_object(
        experiment_custom.get("legacy_plate_meta")
    )
    editable = _metadata_payload(legacy, plate_custom, _EDITABLE_METADATA_KEYS)
    source = _metadata_payload(legacy, experiment_custom, _SOURCE_METADATA_KEYS)
    run_id = _first_text(
        metadata.get("legacy_run_id"), legacy.get("run_id"), view.snapshot.plate_id
    )
    instrument = _first_text(
        metadata.get("instrument"),
        legacy.get("instrument"),
        _lookup(source, "Reader Type"),
    )
    temperature = _first_value(
        metadata.get("temperature"),
        legacy.get("temperature"),
        _lookup(source, "Actual Temperature", "Set Temperature"),
    )
    source_folder = _first_text(
        legacy.get("source_folder_path"),
        legacy.get("source_fingerprint"),
        _lookup(source, "Source Folder"),
    )
    culture_age = _optional_float(_lookup(editable, "Culture_Age_hours")) or 0.0
    culture_volume = _first_value(
        _lookup(editable, "Culture_volume_uL"),
        _lookup(editable, "Culture Volume uL"),
    )
    microplate_id = _first_text(
        _lookup(editable, "Microplate_ID"),
        _lookup(source, "Plate Number"),
        metadata.get("plate_name"),
    )
    return _RunContext(
        view=view,
        run_id=run_id,
        project=_first_text(metadata.get("project"), legacy.get("project")),
        experiment_name=_first_text(
            metadata.get("name"), metadata.get("experiment_name"), legacy.get("experiment_name")
        ),
        experiment_date=_first_text(
            metadata.get("experiment_date"), legacy.get("experiment_date"), legacy.get("exp_date")
        ),
        user=_first_text(
            metadata.get("operator_name"), legacy.get("user_name"), legacy.get("user")
        ),
        instrument=instrument,
        temperature=temperature,
        source_folder=source_folder,
        editable_metadata=editable,
        source_metadata=source,
        start_datetime=_start_datetime(source, legacy),
        culture_age_hours=culture_age,
        culture_volume_ul=culture_volume,
        microplate_id=microplate_id,
    )


def _run_warnings(context: _RunContext) -> tuple[str, ...]:
    warnings: list[str] = []
    if context.start_datetime is None:
        warnings.append(
            f"{context.run_id}: source start date/time is unavailable; Date Time is blank."
        )
    if context.view.background_is_stale:
        warnings.append(
            f"{context.run_id}: current background revision is stale; corrected OD is blank."
        )
    elif not context.view.backgrounds:
        warnings.append(
            f"{context.run_id}: no current background revision is available; corrected OD is blank."
        )
    return tuple(warnings)


def _run_metadata_row(context: _RunContext) -> tuple[object, ...]:
    return (
        context.run_id,
        context.project,
        context.experiment_name,
        context.experiment_date,
        context.user,
        context.instrument,
        context.temperature,
        context.source_folder,
        _json_cell(context.editable_metadata),
        _json_cell(context.source_metadata),
    )


def _measurement_rows(
    context: _RunContext, custom_columns: Sequence[str]
) -> tuple[tuple[object, ...], ...]:
    wells_by_id = {
        _required_text(well.get("well_id"), "Growth export well ID"): well
        for well in context.view.snapshot.wells
    }
    order = {
        _required_text(well.get("well_id"), "Growth export well ID"): index
        for index, well in enumerate(context.view.snapshot.wells)
    }
    observations = sorted(
        context.view.snapshot.raw_observations,
        key=lambda row: (
            order.get(str(row.get("well_id")), len(order)),
            str(row.get("channel", "")),
            _integer(row.get("time_index"), "Growth export time index"),
            _integer(row.get("elapsed_microseconds"), "Growth export elapsed time"),
        ),
    )
    backgrounds = {
        (
            str(row["background_group"]),
            str(row["channel"]),
            _integer(row["time_index"], "Growth background time index"),
            _integer(row["elapsed_microseconds"], "Growth background elapsed time"),
        ): row
        for row in context.view.backgrounds
    }
    result: list[tuple[object, ...]] = []
    for observation in observations:
        well_id = _required_text(observation.get("well_id"), "Growth observation well ID")
        if well_id not in wells_by_id:
            raise ValueError(f"Growth observation references unknown well ID: {well_id}")
        well = wells_by_id[well_id]
        position = _required_text(well.get("position"), "Growth export well position")
        custom = _well_custom(well)
        channel = _required_text(observation.get("channel"), "Growth observation channel")
        time_index = _integer(observation.get("time_index"), "Growth observation time index")
        elapsed = _integer(
            observation.get("elapsed_microseconds"), "Growth observation elapsed time"
        )
        elapsed_minutes = elapsed / 60_000_000
        group = _background_group(well)
        background = backgrounds.get((group, channel, time_index, elapsed))
        raw_od = _optional_float(observation.get("value_raw"))
        if background is None:
            background_mean = None
            background_sd = None
            blank_count = None
            corrected_od = None
            qc_flag = True
            qc_reason = (
                "stale_background_revision"
                if context.view.background_is_stale
                else (
                    "missing_background_revision"
                    if not context.view.backgrounds
                    else "missing_background"
                )
            )
        else:
            background_mean = _optional_float(background.get("mean_value"))
            background_sd = _optional_float(background.get("std_value"))
            blank_count = background.get("blank_count")
            corrected_od = (
                max(_CORRECTED_OD_FLOOR, raw_od - background_mean)
                if raw_od is not None and background_mean is not None
                else None
            )
            qc_status = _first_text(background.get("qc_status"), "missing")
            qc_flag = qc_status != "good"
            qc_reason = "" if not qc_flag else qc_status
        conditions = tuple(_condition_state(well, custom, index) for index in range(1, 4))
        date_time = (
            (context.start_datetime + timedelta(microseconds=elapsed)).isoformat(timespec="seconds")
            if context.start_datetime is not None
            else ""
        )
        result.append(
            (
                _display_name(well, position),
                date_time,
                context.culture_age_hours + elapsed_minutes / 60,
                position[0],
                int(position[1:]),
                context.culture_volume_ul,
                *conditions,
                corrected_od,
                context.microplate_id,
                background_mean,
                background_sd,
                blank_count,
                qc_flag,
                qc_reason,
                context.run_id,
                context.project,
                context.experiment_name,
                position,
                elapsed_minutes,
                channel,
                raw_od,
                bool(well.get("is_blank", False)),
                group,
                well.get("strain"),
                well.get("medium"),
                well.get("replicate"),
                well.get("notes"),
                well.get("raw_label"),
                well.get("display_name"),
                group,
                bool(well.get("plot_selected", False)),
                well.get("grouping_label"),
                well.get("inoculum_size"),
                well.get("inoculum_unit"),
                _first_value(well.get("treatment"), custom.get("treatment_1")),
                _first_value(well.get("concentration"), custom.get("conc_1")),
                _first_value(well.get("concentration_unit"), custom.get("unit_1")),
                custom.get("t0_added_min"),
                *(_custom_cell(_custom_value(custom, column)) for column in custom_columns),
            )
        )
    return tuple(result)


def _metadata_payload(
    legacy: Mapping[str, object],
    fallback: Mapping[str, object],
    keys: Sequence[str],
) -> Mapping[str, object]:
    value = _lookup(legacy, *keys)
    if value is None:
        value = _lookup(fallback, *keys)
    if value is not None:
        return _json_object(value)
    return {
        str(key): item
        for key, item in fallback.items()
        if key not in {"legacy_plate_meta", "legacy_channels"}
    }


def _start_datetime(source: Mapping[str, object], legacy: Mapping[str, object]) -> datetime | None:
    combined = _first_text(
        _lookup(source, "Date Time", "Start Date Time", "Start Datetime"),
        _lookup(legacy, "date_time", "start_datetime"),
    )
    if combined:
        parsed = _parse_datetime(combined)
        if parsed is not None:
            return parsed
    date_value = _first_text(_lookup(source, "Date"), _lookup(legacy, "source_date"))
    time_value = _first_text(_lookup(source, "Time"), _lookup(legacy, "source_time"))
    if not date_value or not time_value:
        return None
    return _parse_datetime(f"{date_value} {time_value}")


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    for format_string in (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, format_string)
        except ValueError:
            continue
    return None


def _condition_state(well: Mapping[str, object], custom: Mapping[str, object], index: int) -> str:
    treatment = custom.get(f"treatment_{index}")
    concentration = custom.get(f"conc_{index}")
    unit = custom.get(f"unit_{index}")
    if index == 1:
        treatment = _first_value(treatment, well.get("treatment"))
        concentration = _first_value(concentration, well.get("concentration"))
        unit = _first_value(unit, well.get("concentration_unit"))
    parts = tuple(text for value in (treatment, concentration, unit) if (text := _cell_text(value)))
    return " ".join(parts)


def _well_custom(well: Mapping[str, object]) -> Mapping[str, object]:
    return {
        **_json_object(well.get("condition_custom_json")),
        **_json_object(well.get("custom_json")),
    }


def _custom_column_names(
    views: Sequence[GrowthRunView], declared: Sequence[str]
) -> tuple[str, ...]:
    """Return stable custom headers, including declared columns with no values."""

    names = {
        str(name).strip().casefold(): str(name).strip() for name in declared if str(name).strip()
    }
    for view in views:
        for well in view.snapshot.wells:
            for raw_name in _well_custom(well):
                name = str(raw_name).strip()
                if name:
                    names.setdefault(name.casefold(), name)
    unavailable = {
        *(header.casefold() for header in GROWTH_MEASUREMENT_HEADERS),
        *(header.casefold() for header in GROWTH_METADATA_HEADERS),
        *(name.casefold() for name in _STRUCTURED_CUSTOM_KEYS),
    }
    return tuple(
        sorted(
            (name for folded, name in names.items() if folded not in unavailable),
            key=str.casefold,
        )
    )


def _custom_cell(value: object) -> object:
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


def _custom_value(custom: Mapping[str, object], column: str) -> object | None:
    expected = column.casefold()
    return next((value for name, value in custom.items() if name.casefold() == expected), None)


def _background_group(well: Mapping[str, object]) -> str:
    return _first_text(well.get("background_group"), "plate")


def _display_name(well: Mapping[str, object], position: str) -> str:
    return _first_text(well.get("display_name"), well.get("raw_label"), position)


def _lookup(mapping: Mapping[str, object], *keys: str) -> object | None:
    normalized = {_normalized_key(str(key)): value for key, value in mapping.items()}
    for key in keys:
        if _normalized_key(key) in normalized:
            return normalized[_normalized_key(key)]
    return None


def _normalized_key(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _json_object(value: object) -> Mapping[str, object]:
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): item for key, item in parsed.items()}


def _json_cell(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _first_text(*values: object) -> str:
    for value in values:
        text = _cell_text(value)
        if text:
            return text
    return ""


def _first_value(*values: object) -> object | None:
    for value in values:
        if value is not None and _cell_text(value):
            return value
    return None


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _required_text(value: object, field: str) -> str:
    text = _cell_text(value)
    if not text:
        raise ValueError(f"{field} cannot be empty")
    return text


def _optional_float(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(str(value))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value
