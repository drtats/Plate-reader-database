"""Multi-run Growth CSV export matching the laboratory analysis contract."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from string import Formatter

from plate_reader.application.contracts import Actor, AssayType, PlateId
from plate_reader.application.services.growth_cultivation import (
    GrowthCultivationRepository,
    plan_cultivation_experiment_codes,
)
from plate_reader.application.services.growth_cultivation_replicates import (
    ConditionReplicate,
    plan_export_condition_replicates,
)
from plate_reader.application.services.growth_workflow import (
    GrowthRunView,
    LoadGrowthRunService,
)
from plate_reader.application.services.layout_columns import ListLayoutColumnsService
from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import WellPosition
from plate_reader.domain.growth.cultivation import (
    DEFAULT_CULTIVATION_PATTERN,
    LEGACY_CULTIVATION_PATTERN,
    format_cultivation_id,
    generate_cultivation_id,
    normalize_cultivation_experiment_code,
)
from plate_reader.domain.growth.cultivation_conditions import (
    cultivation_condition_key,
    matching_concentration,
    primary_condition_value,
    validate_concentration_precision,
)
from plate_reader.domain.growth.units import normalize_growth_unit

GROWTH_REGISTRY_MEASUREMENT_HEADERS = (
    "Cultivation ID",
    "Saved cultivation ID",
    "Cultivation experiment code",
    "Cultivation ID pattern",
    "Cultivation replicate",
    "Cultivation replicate scope",
    "Cultivation condition key",
    "Culture_Age_h",
)

GROWTH_REGISTRY_METADATA_HEADERS = (
    "Cultivation",
    "SavedCultivation",
    "Local_Cultivation_ID",
    "InoculationDateTime",
    "ProgramMetric",
    "CultivationExperiment",
    "Comment",
    "Team_Code",
    "Strain",
    "Strain/Strain_Aliases",
    "CultivationSystemCode",
    "CultivationRun",
    "Replicate",
    "Vessel_Alphabetical_ID",
    "Vessel_Numeric_ID",
    "Objective",
    "Condition",
    "Media",
    "EquipmentMakeModel",
    "CultivationProtocol",
    "SampleAnalysisProtocol",
    "CultivationExperimentCode",
    "CultivationIDPattern",
    "CultivationReplicate",
    "LocalReplicate",
    "CultivationReplicateScope",
    "CultivationConditionKey",
    "CultivationConditionFields",
    "CultivationReplicateMode",
)

GROWTH_MATCHING_CONCENTRATION_HEADERS = (
    "Concentration matching significant figures",
    "Matching concentration",
    "Matching concentration 2",
    "Matching concentration 3",
)

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
# layout column while retaining the established legacy block.
GROWTH_ADDITIONAL_LAYOUT_HEADERS = (
    "Local replicate",
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
    "Treatment 2",
    "Concentration 2",
    "Concentration unit 2",
    "Treatment 3",
    "Concentration 3",
    "Concentration unit 3",
)

GROWTH_MEASUREMENT_HEADERS = (
    *GROWTH_REGISTRY_MEASUREMENT_HEADERS,
    *_LEGACY_GROWTH_MEASUREMENT_HEADERS,
    *GROWTH_ADDITIONAL_LAYOUT_HEADERS,
    *GROWTH_MATCHING_CONCENTRATION_HEADERS,
)

GROWTH_METADATA_HEADERS = (
    *GROWTH_REGISTRY_METADATA_HEADERS,
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
    "Treatment",
    "Concentration",
    "Concentration unit",
    "Treatment 2",
    "Concentration 2",
    "Concentration unit 2",
    "Treatment 3",
    "Concentration 3",
    "Concentration unit 3",
    "Well",
    "Well Metadata JSON",
    "Experiment Metadata JSON",
    "Plate Metadata JSON",
    *GROWTH_MATCHING_CONCENTRATION_HEADERS,
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
_CULTIVATION_PATTERN_FIELDS = frozenset(
    {"team", "strain", "system", "run", "experiment", "well", "replicate"}
)


@dataclass(frozen=True, slots=True)
class ExportCultivationSettings:
    """Output-only ID settings; blank components fall back to saved well/run metadata."""

    pattern: str | None = DEFAULT_CULTIVATION_PATTERN
    team_code: str = ""
    system_code: str = ""


@dataclass(frozen=True, slots=True)
class ExportGrowthTabularData:
    actor: Actor
    plate_ids: tuple[PlateId, ...]
    assign_selected_replicates: bool = False
    condition_fields: tuple[str, ...] = ()
    cultivation_settings: ExportCultivationSettings | None = None
    concentration_significant_figures: int | None = None


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
    replicate_preview: tuple[dict[str, object], ...] = ()
    effective_condition_fields: tuple[str, ...] = ()


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
    effective_identities: Mapping[str, Mapping[str, object]] | None = None
    concentration_significant_figures: int | None = None


class ExportGrowthTabularDataService:
    """Load selected Growth runs and create the two read-only CSV artifacts."""

    def __init__(self, repository: GrowthCultivationRepository) -> None:
        self.repository = repository

    def execute(self, command: ExportGrowthTabularData) -> GrowthTabularExportBundle:
        _validate_matching_mode(
            command.assign_selected_replicates, command.concentration_significant_figures
        )
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
        experiment_codes = (
            plan_cultivation_experiment_codes(self.repository.growth_cultivation_codes())
            if command.assign_selected_replicates and command.cultivation_settings is not None
            else None
        )
        return export_growth_tabular_data(
            views,
            custom_columns=custom_columns,
            assign_selected_replicates=command.assign_selected_replicates,
            condition_fields=command.condition_fields,
            cultivation_settings=command.cultivation_settings,
            experiment_codes=experiment_codes,
            concentration_significant_figures=command.concentration_significant_figures,
        )


def export_growth_tabular_data(
    views: Sequence[GrowthRunView],
    *,
    custom_columns: Sequence[str] = (),
    assign_selected_replicates: bool = False,
    condition_fields: tuple[str, ...] = (),
    cultivation_settings: ExportCultivationSettings | None = None,
    experiment_codes: Mapping[str, str] | None = None,
    concentration_significant_figures: int | None = None,
) -> GrowthTabularExportBundle:
    """Build deterministic multi-run measurement and metadata CSV files."""

    _validate_matching_mode(assign_selected_replicates, concentration_significant_figures)
    if not views:
        raise ValueError("Growth tabular export requires at least one run")
    plate_ids = tuple(str(view.snapshot.plate_id) for view in views)
    if len(set(plate_ids)) != len(plate_ids):
        raise ValueError("Growth tabular export views must have unique plate IDs")

    if cultivation_settings is not None and not assign_selected_replicates:
        raise _cultivation_error(
            "Cultivation generation requires selected-run replicate assignment"
        )
    if cultivation_settings is not None and cultivation_settings.pattern is not None:
        _pattern_fields(cultivation_settings.pattern)
        if "R{replicate}" not in cultivation_settings.pattern:
            raise _cultivation_error("Cultivation ID pattern must include the R{replicate} suffix")
    contexts = tuple(_run_context(view) for view in views)
    if cultivation_settings is not None and experiment_codes is None:
        experiment_codes = _selected_experiment_codes(contexts)
    selection_warnings: tuple[str, ...] = ()
    replicate_preview: tuple[dict[str, object], ...] = ()
    effective_condition_fields: tuple[str, ...] = ()
    if assign_selected_replicates:
        contexts, selection_warnings, replicate_preview, effective_condition_fields = (
            _selection_contexts(
                contexts,
                condition_fields,
                cultivation_settings,
                experiment_codes or {},
                concentration_significant_figures,
            )
        )
    exported_custom_columns = _custom_column_names(views, custom_columns)
    single_run_name = _single_run_filename_stem(contexts)

    measurement_stream = io.StringIO(newline="")
    metadata_stream = io.StringIO(newline="")
    measurement_writer = csv.writer(measurement_stream, lineterminator="\n")
    metadata_writer = csv.writer(metadata_stream, lineterminator="\n")
    measurement_writer.writerow((*GROWTH_MEASUREMENT_HEADERS, *exported_custom_columns))
    metadata_writer.writerow((*GROWTH_METADATA_HEADERS, *exported_custom_columns))

    measurement_count = 0
    metadata_count = 0
    warnings: list[str] = list(selection_warnings)
    seen_cultivations: set[str] = set()
    for context in contexts:
        warnings.extend(_run_warnings(context))
        missing_ids = 0
        for well in context.view.snapshot.wells:
            registry = _cultivation_metadata(context, well)
            cultivation = str(registry["Cultivation"])
            if cultivation:
                if cultivation in seen_cultivations:
                    raise _cultivation_error(
                        f"Duplicate cultivation ID in selected runs: {cultivation}"
                    )
                seen_cultivations.add(cultivation)
            else:
                missing_ids += 1
            metadata_writer.writerow(
                (
                    *(registry[header] for header in GROWTH_REGISTRY_METADATA_HEADERS),
                    *_run_metadata_row(context),
                    *_separate_conditions(well),
                    well.get("position"),
                    _json_cell(_well_custom(well)),
                    _json_cell(
                        _json_object(context.view.snapshot.metadata.get("experiment_custom_json"))
                    ),
                    _json_cell(
                        _json_object(context.view.snapshot.metadata.get("plate_custom_json"))
                    ),
                    *_matching_concentration_row(well, context.concentration_significant_figures),
                    *(
                        _custom_cell(_custom_value(_well_custom(well), column))
                        for column in exported_custom_columns
                    ),
                )
            )
            metadata_count += 1
        if missing_ids:
            warnings.append(
                f"{context.run_id}: {missing_ids} wells have no cultivation ID; "
                "check the export ID settings and saved well strain before registry submission."
            )
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
        replicate_preview=replicate_preview,
        effective_condition_fields=effective_condition_fields,
    )


def _validate_matching_mode(assign_replicates: bool, precision: int | None) -> None:
    validate_concentration_precision(precision)
    if precision is not None and not assign_replicates:
        raise _cultivation_error(
            "Concentration rounding requires selected-run replicate assignment"
        )


def _matching_concentration_row(
    well: Mapping[str, object],
    precision: int | None,
) -> tuple[object, ...]:
    """Expose comparison doses alongside unchanged entered doses in each CSV."""

    conditions = _separate_conditions(well)
    return (
        "exact" if precision is None else precision,
        *(matching_concentration(conditions[index], precision) for index in (1, 4, 7)),
    )


def _concentration_summary(well: Mapping[str, object], precision: int | None) -> str:
    conditions = _separate_conditions(well)
    return "; ".join(
        " ".join(
            part
            for part in (
                _cell_text(conditions[index]),
                matching_concentration(conditions[index + 1], precision),
                _cell_text(conditions[index + 2]),
            )
            if part
        )
        for index in (0, 3, 6)
        if any(_cell_text(value) for value in conditions[index : index + 3])
    )


def _selected_experiment_codes(contexts: Sequence[_RunContext]) -> Mapping[str, str]:
    """Use available views as the numbering universe for direct in-memory exports."""

    rows: list[dict[str, object]] = []
    for context in contexts:
        snapshot = context.view.snapshot
        plate_custom = _json_object(snapshot.metadata.get("plate_custom_json"))
        rows.append(
            {
                "record_type": "plate",
                "plate_id": str(snapshot.plate_id),
                "experiment_date": snapshot.metadata.get("experiment_date"),
                "created_at": snapshot.metadata.get("created_at"),
                "custom_json": _json_object(plate_custom.get("cultivation_registry")),
            }
        )
        rows.extend(
            {
                "record_type": "well",
                "plate_id": str(snapshot.plate_id),
                "custom_json": _well_custom(well),
            }
            for well in snapshot.wells
        )
    return plan_cultivation_experiment_codes(rows)


def _selection_contexts(
    contexts: tuple[_RunContext, ...],
    condition_fields: tuple[str, ...],
    settings: ExportCultivationSettings | None,
    experiment_codes: Mapping[str, str],
    concentration_significant_figures: int | None,
) -> tuple[
    tuple[_RunContext, ...],
    tuple[str, ...],
    tuple[dict[str, object], ...],
    tuple[str, ...],
]:
    rows: list[dict[str, object]] = []
    for context in contexts:
        metadata = context.view.snapshot.metadata
        plate_id = str(context.view.snapshot.plate_id)
        for well in context.view.snapshot.wells:
            rows.append(
                {
                    **well,
                    "plate_id": plate_id,
                    "experiment_date": metadata.get("experiment_date"),
                    "created_at": metadata.get("created_at"),
                    "deleted_at": metadata.get("deleted_at"),
                    "temperature": metadata.get("temperature"),
                    "temperature_unit": metadata.get("temperature_unit"),
                    "plate_custom_json": metadata.get("plate_custom_json"),
                    "experiment_custom_json": metadata.get("experiment_custom_json"),
                }
            )
    plans = plan_export_condition_replicates(
        rows,
        extra_fields=condition_fields,
        concentration_significant_figures=concentration_significant_figures,
    )
    # Summary for display only; each well's key retains its own run's matching fields.
    fields = tuple(
        sorted(set(condition_fields).union(*(plan.extra_fields for plan in plans.values())))
    )
    selected_contexts: list[_RunContext] = []
    previews: list[dict[str, object]] = []
    missing_counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for context in contexts:
        plate_id = str(context.view.snapshot.plate_id)
        identities: dict[str, Mapping[str, object]] = {}
        for well in context.view.snapshot.wells:
            raw_position = _required_text(well.get("position"), "Growth export well position")
            position = WellPosition.parse(raw_position).label
            plan = plans.get((plate_id, position))
            identity, missing = _selection_identity(
                context, well, plan, settings, experiment_codes.get(plate_id, "")
            )
            identities[position] = identity
            if missing:
                key = (context.run_id, missing)
                missing_counts[key] = missing_counts.get(key, 0) + 1
            if plan is not None:
                previews.append(
                    {
                        "Run ID": plate_id,
                        "Well": position,
                        "Strain": _first_text(well.get("strain")),
                        "Local replicate": well.get("replicate"),
                        "Export replicate": plan.replicate,
                        "Concentration matching": "Exact values"
                        if concentration_significant_figures is None
                        else f"{concentration_significant_figures} significant figures",
                        "Entered concentrations": _concentration_summary(well, None),
                        "Matching concentrations": _concentration_summary(
                            well, concentration_significant_figures
                        ),
                        "Experiment number": identity["CultivationExperimentCode"],
                        "Matching wells": plan.matching_wells,
                        "Matching fields": ", ".join(plan.extra_fields),
                        "Saved cultivation ID": identity["SavedCultivation"],
                        "Cultivation ID": identity["Cultivation"],
                    }
                )
        selected_contexts.append(
            replace(
                context,
                effective_identities=identities,
                concentration_significant_figures=concentration_significant_figures,
            )
        )
    warnings = tuple(
        f"{run_id}: {count} selected well(s) have no export cultivation ID; missing "
        f"{', '.join(missing)}."
        for (run_id, missing), count in sorted(missing_counts.items())
    )
    return tuple(selected_contexts), warnings, tuple(previews), fields


def _selection_identity(
    context: _RunContext,
    well: Mapping[str, object],
    plan: ConditionReplicate | None,
    settings: ExportCultivationSettings | None,
    suggested_code: str,
) -> tuple[dict[str, object], tuple[str, ...]]:
    custom = _well_custom(well)
    plate_custom = _json_object(context.view.snapshot.metadata.get("plate_custom_json"))
    shared = _json_object(plate_custom.get("cultivation_registry"))
    saved = _first_text(custom.get("Cultivation"))
    if plan is None:
        return (
            {
                "Cultivation": "",
                "SavedCultivation": saved,
                "CultivationReplicate": "",
                "CultivationReplicateScope": "",
                "CultivationConditionKey": "",
                "CultivationConditionFields": "",
                "CultivationReplicateMode": "",
                "Replicate": well.get("replicate"),
                "LocalReplicate": well.get("replicate"),
            },
            (),
        )

    pattern = (
        settings.pattern
        if settings is not None and settings.pattern is not None
        else _selection_pattern(custom, shared, saved)
    )
    fields = _pattern_fields(pattern)
    components = {
        "team": _first_text(
            settings.team_code if settings is not None else "",
            custom.get("Team_Code"),
            shared.get("Team_Code"),
        ),
        "strain": _first_text(well.get("strain")),
        "system": _first_text(
            settings.system_code if settings is not None else "",
            custom.get("CultivationSystemCode"),
            shared.get("CultivationSystemCode"),
        ),
        "run": _first_text(
            custom.get("CultivationRun"), shared.get("CultivationRun"), suggested_code
        ),
        "experiment": _first_text(
            custom.get("CultivationExperimentCode"),
            shared.get("CultivationExperimentCode"),
            suggested_code,
        ),
    }
    if components["experiment"]:
        components["experiment"] = normalize_cultivation_experiment_code(
            pattern, components["experiment"]
        )
    missing_fields = {field for field in fields if field in components and not components[field]}
    if not components["strain"]:
        missing_fields.add("strain")
    missing = tuple(sorted(missing_fields))
    cultivation = ""
    if not missing:
        cultivation = format_cultivation_id(
            pattern,
            team_code=components["team"],
            strain=components["strain"],
            system_code=components["system"],
            cultivation_run=components["run"],
            replicate=plan.replicate,
            experiment_code=components["experiment"],
            position=_required_text(well.get("position"), "Growth export well position"),
        )
    run = components["run"]
    if run.isascii() and run.isdecimal() and run.lstrip("0"):
        run = run.lstrip("0").zfill(3)
    return (
        {
            "Cultivation": cultivation,
            "SavedCultivation": saved,
            "Team_Code": components["team"],
            "CultivationSystemCode": components["system"],
            "CultivationRun": run,
            "CultivationExperimentCode": components["experiment"],
            "CultivationIDPattern": pattern,
            "Replicate": plan.replicate,
            "LocalReplicate": well.get("replicate"),
            "CultivationReplicate": plan.replicate,
            "CultivationReplicateScope": plan.scope,
            "CultivationConditionKey": plan.condition_key,
            "CultivationConditionFields": json.dumps(plan.extra_fields, separators=(",", ":")),
            "CultivationReplicateMode": "export_run",
        },
        missing,
    )


def _selection_pattern(
    custom: Mapping[str, object], shared: Mapping[str, object], saved: str
) -> str:
    if "CultivationIDPattern" in custom:
        pattern = custom["CultivationIDPattern"]
    elif saved:
        pattern = LEGACY_CULTIVATION_PATTERN
    elif "CultivationIDPattern" in shared:
        pattern = shared["CultivationIDPattern"]
    else:
        pattern = DEFAULT_CULTIVATION_PATTERN
    if not isinstance(pattern, str) or not pattern:
        raise _cultivation_error("Cultivation ID pattern must be non-empty")
    return pattern


def _pattern_fields(pattern: str) -> frozenset[str]:
    if ":" in pattern:
        raise _cultivation_error("Cultivation ID pattern has an unsupported format specification")
    try:
        parts = tuple(Formatter().parse(pattern))
    except ValueError as error:
        raise _cultivation_error("Cultivation ID pattern has invalid braces") from error
    fields: set[str] = set()
    for _, field, spec, conversion in parts:
        if field is None:
            continue
        if field not in _CULTIVATION_PATTERN_FIELDS or spec or conversion is not None:
            raise _cultivation_error("Cultivation ID pattern has an unsupported placeholder")
        fields.add(field)
    if "replicate" not in fields:
        raise _cultivation_error("Cultivation ID pattern must include {replicate} for export R")
    return frozenset(fields)


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
        registry = _cultivation_metadata(context, well)
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
                registry["Cultivation"],
                registry["SavedCultivation"],
                registry["CultivationExperimentCode"],
                registry["CultivationIDPattern"],
                registry["CultivationReplicate"],
                registry["CultivationReplicateScope"],
                registry["CultivationConditionKey"],
                _culture_age(context, registry, elapsed),
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
                registry["Replicate"],
                well.get("notes"),
                well.get("replicate"),
                well.get("raw_label"),
                well.get("display_name"),
                group,
                bool(well.get("plot_selected", False)),
                well.get("grouping_label"),
                well.get("inoculum_size"),
                normalize_growth_unit(well.get("inoculum_unit")),
                primary_condition_value(well, custom, "treatment", "treatment_1"),
                primary_condition_value(well, custom, "concentration", "conc_1"),
                normalize_growth_unit(
                    primary_condition_value(well, custom, "concentration_unit", "unit_1")
                ),
                custom.get("t0_added_min"),
                *_separate_conditions(well)[3:],
                *_matching_concentration_row(well, context.concentration_significant_figures),
                *(_custom_cell(_custom_value(custom, column)) for column in custom_columns),
            )
        )
    return tuple(result)


def _separate_conditions(well: Mapping[str, object]) -> tuple[object, ...]:
    custom = _well_custom(well)
    return (
        primary_condition_value(well, custom, "treatment", "treatment_1"),
        primary_condition_value(well, custom, "concentration", "conc_1"),
        normalize_growth_unit(
            primary_condition_value(well, custom, "concentration_unit", "unit_1")
        ),
        *(
            normalize_growth_unit(custom.get(f"{field}_{index}"))
            if field == "unit"
            else custom.get(f"{field}_{index}")
            for index in (2, 3)
            for field in ("treatment", "conc", "unit")
        ),
    )


def _cultivation_metadata(context: _RunContext, well: Mapping[str, object]) -> dict[str, object]:
    """Resolve descriptive defaults, keeping saved per-well identity authoritative."""

    plate_custom = _json_object(context.view.snapshot.metadata.get("plate_custom_json"))
    shared = _json_object(plate_custom.get("cultivation_registry"))
    custom = _well_custom(well)
    values = {**shared, **custom}
    result: dict[str, object] = {
        header: _first_text(values.get(header)) for header in GROWTH_REGISTRY_METADATA_HEADERS
    }
    position = _required_text(well.get("position"), "Growth export well position")
    result.update(
        {
            "Cultivation": _first_text(custom.get("Cultivation")),
            "SavedCultivation": _first_text(custom.get("Cultivation")),
            "CultivationExperimentCode": _first_text(custom.get("CultivationExperimentCode")),
            "CultivationIDPattern": _first_text(custom.get("CultivationIDPattern")),
            "Strain": _first_text(well.get("strain")),
            "Replicate": well.get("replicate"),
            "LocalReplicate": well.get("replicate"),
            "CultivationReplicate": well.get("replicate") if custom.get("Cultivation") else "",
            "CultivationReplicateScope": _first_text(custom.get("CultivationReplicateScope")),
            "CultivationConditionKey": _first_text(custom.get("CultivationConditionKey")),
            "CultivationConditionFields": "",
            "CultivationReplicateMode": _first_text(custom.get("CultivationReplicateMode")),
            "Media": _first_text(well.get("medium")),
            "Local_Cultivation_ID": _first_text(
                custom.get("Local_Cultivation_ID"),
                f"{context.microplate_id} {position[0]}{int(position[1:]):02d}".strip(),
            ),
            "Vessel_Alphabetical_ID": position[0],
            "Vessel_Numeric_ID": int(position[1:]),
            "Comment": _first_text(custom.get("Comment"), well.get("notes"), shared.get("Comment")),
            "EquipmentMakeModel": _first_text(values.get("EquipmentMakeModel"), context.instrument),
        }
    )
    if context.effective_identities is not None:
        override = context.effective_identities.get(WellPosition.parse(position).label)
        if override is None:
            raise _cultivation_error(f"{position}: missing export selection identity")
        result.update(override)
        return result
    cultivation = str(result["Cultivation"])
    if cultivation:
        if custom.get("CultivationReplicateMode") == "condition":
            id_replicate = _integer(custom.get("CultivationReplicate"), "Cultivation replicate")
            if id_replicate < 1:
                raise _cultivation_error("Cultivation replicate must be positive")
            fields = custom.get("CultivationConditionFields", [])
            if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
                raise _cultivation_error("Cultivation condition fields must be a list of names")
            scope = str(result["CultivationReplicateScope"])
            current_key = cultivation_condition_key(
                {**well, "plate_id": str(context.view.snapshot.plate_id)},
                context.view.snapshot.metadata,
                scope,
                tuple(fields),
            )
            if current_key != result["CultivationConditionKey"]:
                raise _cultivation_error(
                    f"{position}: saved cultivation conditions changed; "
                    "preview and save cultivation IDs again in Metadata."
                )
            result["Replicate"] = id_replicate
            result["CultivationReplicate"] = id_replicate
            result["CultivationConditionFields"] = json.dumps(fields, separators=(",", ":"))
        else:
            id_replicate = _integer(well.get("replicate"), "Cultivation biological replicate")
        # Condition-based IDs use the saved global number, independent of local labels.
        if "CultivationIDPattern" in custom:
            expected = format_cultivation_id(
                str(result["CultivationIDPattern"]),
                team_code=_first_text(custom.get("Team_Code")),
                strain=str(result["Strain"]),
                system_code=_first_text(custom.get("CultivationSystemCode")),
                cultivation_run=_first_text(custom.get("CultivationRun")),
                replicate=id_replicate,
                experiment_code=str(result["CultivationExperimentCode"]),
                position=position,
            )
        else:
            expected = generate_cultivation_id(
                _first_text(custom.get("Team_Code")),
                str(result["Strain"]),
                _first_text(custom.get("CultivationSystemCode")),
                _first_text(custom.get("CultivationRun")),
                id_replicate,
            )
            result["CultivationIDPattern"] = LEGACY_CULTIVATION_PATTERN
        if cultivation != expected:
            raise _cultivation_error(
                f"{position}: saved cultivation ID no longer matches the layout; "
                "regenerate cultivation IDs in Metadata."
            )
    return result


def _cultivation_error(message: str) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message))


def _culture_age(context: _RunContext, registry: Mapping[str, object], elapsed: int) -> float:
    inoculation_text = _first_text(registry.get("InoculationDateTime"))
    if inoculation_text:
        inoculation = _parse_datetime(inoculation_text)
        if inoculation is None:
            raise _cultivation_error("InoculationDateTime must be a valid date and time")
        if context.start_datetime is not None:
            try:
                return (
                    context.start_datetime + timedelta(microseconds=elapsed) - inoculation
                ).total_seconds() / 3600
            except TypeError as error:
                raise _cultivation_error(
                    "Source start and inoculation times must use compatible time zones"
                ) from error
    return context.culture_age_hours + elapsed / 3_600_000_000


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
        treatment = primary_condition_value(well, custom, "treatment", "treatment_1")
        concentration = primary_condition_value(well, custom, "concentration", "conc_1")
        unit = primary_condition_value(well, custom, "concentration_unit", "unit_1")
    unit = normalize_growth_unit(unit)
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
