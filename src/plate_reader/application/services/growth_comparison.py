"""Metadata-only well discovery and explicit raw Growth comparison rendering."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from numbers import Real
from typing import Protocol

from plate_reader.application.contracts import Actor, AssayType, PlateId, Role
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_plotting import (
    GrowthPlotData,
    GrowthPlotPoint,
    PrepareGrowthPlotDataService,
)
from plate_reader.domain.common import DomainIssue


@dataclass(frozen=True, slots=True)
class GrowthComparisonWell:
    """A single Growth well and its queryable metadata, without measurements."""

    plate_id: str
    well_id: str
    position: str
    display_name: str | None = None
    strain: str | None = None
    treatment: str | None = None
    concentration: int | float | Decimal | None = None
    concentration_unit: str | None = None
    medium: str | None = None
    replicate: int | None = None
    grouping_label: str | None = None
    inoculum_size: int | float | Decimal | None = None
    inoculum_unit: str | None = None
    is_blank: bool = False

    def __post_init__(self) -> None:
        for name in ("plate_id", "well_id", "position"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"Growth comparison well {name} cannot be empty")
            object.__setattr__(self, name, value)
        for name in (
            "display_name",
            "strain",
            "treatment",
            "concentration_unit",
            "medium",
            "grouping_label",
            "inoculum_unit",
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, _trimmed_text(value))
        if self.replicate is not None and (
            isinstance(self.replicate, bool)
            or not isinstance(self.replicate, int)
            or self.replicate < 1
        ):
            raise ValueError("Growth comparison replicate must be a positive integer when present")
        if self.concentration is not None:
            _normalized_number(self.concentration, "concentration")
        if self.inoculum_size is not None:
            _normalized_number(self.inoculum_size, "inoculum size")


@dataclass(frozen=True, slots=True)
class GrowthComparisonPlate:
    """The condition-only well index for one selected Growth plate."""

    plate_id: str
    wells: tuple[GrowthComparisonWell, ...]
    experiment_name: str | None = None
    plate_name: str | None = None

    def __post_init__(self) -> None:
        plate_id = str(self.plate_id).strip()
        if not plate_id:
            raise ValueError("Growth comparison plate_id cannot be empty")
        wells = tuple(self.wells)
        if any(well.plate_id != plate_id for well in wells):
            raise ValueError("Every Growth comparison well must belong to its containing plate")
        well_ids = tuple(well.well_id for well in wells)
        if len(set(well_ids)) != len(well_ids):
            raise ValueError("Growth comparison plate contains duplicate well_id values")
        object.__setattr__(self, "plate_id", plate_id)
        object.__setattr__(self, "wells", wells)
        object.__setattr__(self, "experiment_name", _trimmed_text(self.experiment_name))
        object.__setattr__(self, "plate_name", _trimmed_text(self.plate_name))


@dataclass(frozen=True, slots=True)
class GrowthWellSearchFilter:
    """Frozen, normalized predicates for a local metadata-only well search.

    Text tuples use OR matching within one field; every populated field is ANDed
    with every other populated field.  ``source_plate_ids`` narrows the already
    selected index and does not authorize or fetch any new plate.
    """

    source_plate_ids: tuple[str, ...] = ()
    text: str | None = None
    strains: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    concentration_min: int | float | Decimal | None = None
    concentration_max: int | float | Decimal | None = None
    concentration_units: tuple[str, ...] = ()
    media: tuple[str, ...] = ()
    replicates: tuple[int, ...] = ()
    grouping_labels: tuple[str, ...] = ()
    inoculum_sizes: tuple[int | float | Decimal, ...] = ()
    inoculum_units: tuple[str, ...] = ()
    include_blank_wells: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_plate_ids", _unique_text_values(self.source_plate_ids))
        object.__setattr__(self, "text", _trimmed_text(self.text))
        for name in (
            "strains",
            "treatments",
            "concentration_units",
            "media",
            "grouping_labels",
            "inoculum_units",
        ):
            object.__setattr__(self, name, _unique_text_values(getattr(self, name)))
        replicates = tuple(self.replicates)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in replicates
        ):
            raise ValueError("Growth comparison replicates must be positive integers")
        object.__setattr__(self, "replicates", tuple(sorted(set(replicates))))
        inoculum_sizes = tuple(
            _normalized_number(value, "inoculum size") for value in self.inoculum_sizes
        )
        object.__setattr__(self, "inoculum_sizes", tuple(sorted(set(inoculum_sizes))))
        minimum = (
            _normalized_number(self.concentration_min, "concentration minimum")
            if self.concentration_min is not None
            else None
        )
        maximum = (
            _normalized_number(self.concentration_max, "concentration maximum")
            if self.concentration_max is not None
            else None
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("Growth comparison concentration minimum cannot exceed maximum")
        if (minimum is not None or maximum is not None) and len(self.concentration_units) != 1:
            raise ValueError(
                "Growth comparison concentration bounds require exactly one concentration unit"
            )
        object.__setattr__(self, "concentration_min", minimum)
        object.__setattr__(self, "concentration_max", maximum)
        if not isinstance(self.include_blank_wells, bool):
            raise ValueError("Growth comparison include_blank_wells must be boolean")


@dataclass(frozen=True, slots=True)
class GrowthWellSearchResult:
    """The first 500 deterministic metadata matches and the untruncated total."""

    wells: tuple[GrowthComparisonWell, ...]
    total: int
    truncated: bool
    quick_stats: GrowthComparisonQuickStats | None = None

    def __post_init__(self) -> None:
        if self.total < len(self.wells) or self.total < 0:
            raise ValueError("Growth comparison search total is invalid")
        if len(self.wells) > _SEARCH_RESULT_LIMIT:
            raise ValueError("Growth comparison search result exceeds its fixed limit")
        if self.truncated != (self.total > len(self.wells)):
            raise ValueError("Growth comparison search truncation state is inconsistent")
        if self.quick_stats is not None and self.quick_stats.total_wells != self.total:
            raise ValueError("Growth comparison quick stats must cover every matching well")


@dataclass(frozen=True, slots=True)
class GrowthComparisonSummaryField:
    """One supported layout-metadata dimension for comparison quick stats."""

    key: str
    label: str


@dataclass(frozen=True, slots=True)
class GrowthComparisonQuickStat:
    """Counts for one unique combination of selected layout metadata."""

    values: tuple[str, ...]
    well_count: int
    plate_count: int

    def __post_init__(self) -> None:
        if self.well_count < 1 or self.plate_count < 1 or self.plate_count > self.well_count:
            raise ValueError("Growth comparison quick-stat counts are invalid")


@dataclass(frozen=True, slots=True)
class GrowthComparisonQuickStats:
    """Condition groups calculated from actual wells, never replicate labels."""

    fields: tuple[GrowthComparisonSummaryField, ...]
    groups: tuple[GrowthComparisonQuickStat, ...]
    total_wells: int

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("Growth comparison quick stats require at least one field")
        if (
            self.total_wells < 0
            or sum(group.well_count for group in self.groups) != self.total_wells
        ):
            raise ValueError("Growth comparison quick-stat total is invalid")


_SUMMARY_FIELDS = (
    GrowthComparisonSummaryField("display_name", "Display name"),
    GrowthComparisonSummaryField("strain", "Strain"),
    GrowthComparisonSummaryField("treatment", "Treatment"),
    GrowthComparisonSummaryField("concentration", "Concentration"),
    GrowthComparisonSummaryField("medium", "Medium"),
    GrowthComparisonSummaryField("grouping_label", "Group"),
    GrowthComparisonSummaryField("inoculum_size", "Inoculum size"),
    GrowthComparisonSummaryField("is_blank", "Blank status"),
)
_SUMMARY_FIELDS_BY_KEY = {field.key: field for field in _SUMMARY_FIELDS}


def growth_comparison_summary_fields() -> tuple[GrowthComparisonSummaryField, ...]:
    """Return metadata dimensions that may define an empirical replicate group.

    The stored ``replicate`` label is deliberately absent: quick-stat counts are
    derived from the number of wells sharing the selected condition metadata.
    """

    return _SUMMARY_FIELDS


@dataclass(frozen=True, slots=True)
class GrowthComparisonPlotResult:
    """Raw comparison plot data loaded only after an explicit render request."""

    plot_data: GrowthPlotData
    cache_key: str
    plate_count: int
    well_count: int


class GrowthComparisonWellIndexRepository(Protocol):
    """Authorized metadata-only read surface used before curve rendering."""

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def growth_comparison_wells(
        self, plate_ids: Sequence[PlateId]
    ) -> Sequence[Mapping[str, object]]:
        """Return requested wells and condition metadata, never measurements."""


class LoadGrowthComparisonWellIndexService:
    """Load the selected Growth well index in one authorized repository call."""

    def __init__(self, repository: GrowthComparisonWellIndexRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, plate_ids: Sequence[PlateId]
    ) -> tuple[GrowthComparisonPlate, ...]:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        requested_ids = _validated_requested_plate_ids(plate_ids)
        return _plates_from_well_rows(
            requested_ids, self.repository.growth_comparison_wells(requested_ids)
        )


class SearchGrowthComparisonWellsService:
    """Filter an already loaded comparison index without repository access."""

    def execute(
        self,
        plates: Sequence[GrowthComparisonPlate],
        filters: GrowthWellSearchFilter | None = None,
        summary_fields: Sequence[str] = (),
    ) -> GrowthWellSearchResult:
        index = _validated_plate_index(plates, require_two=False)
        filters = filters or GrowthWellSearchFilter()
        if not isinstance(filters, GrowthWellSearchFilter):
            raise ValueError("Growth comparison filters must be GrowthWellSearchFilter")
        allowed_ids = set(filters.source_plate_ids) if filters.source_plate_ids else None
        if allowed_ids is not None:
            unknown = allowed_ids - {plate.plate_id for plate in index}
            if unknown:
                raise ValueError(
                    "Growth comparison filter references a plate outside the supplied index: "
                    + ", ".join(sorted(unknown))
                )
        matches = tuple(
            well
            for plate in index
            if allowed_ids is None or plate.plate_id in allowed_ids
            for well in sorted(plate.wells, key=_well_sort_key)
            if _matches_filter(well, filters)
        )
        quick_stats = (
            _growth_comparison_quick_stats(matches, summary_fields) if summary_fields else None
        )
        return GrowthWellSearchResult(
            wells=matches[:_SEARCH_RESULT_LIMIT],
            total=len(matches),
            truncated=len(matches) > _SEARCH_RESULT_LIMIT,
            quick_stats=quick_stats,
        )


class GrowthComparisonPlotRepository(Protocol):
    """Minimal authorized raw-data surface for an explicit comparison render."""

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...

    def plate_cache_token(self, plate_id: PlateId) -> str | None: ...


class LoadGrowthComparisonPlotService:
    """Load raw observations only for explicit, individually selected wells."""

    def __init__(self, repository: GrowthComparisonPlotRepository) -> None:
        self.repository = repository
        self.plot_preparer = PrepareGrowthPlotDataService()

    def execute(
        self,
        actor: Actor,
        plate_index: Sequence[GrowthComparisonPlate],
        selected_wells: Sequence[GrowthComparisonWell],
    ) -> GrowthComparisonPlotResult:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        plates = _validated_plate_index(plate_index, require_two=False)
        selected = _validated_selected_wells(plates, selected_wells)
        selected_by_plate: dict[str, list[GrowthComparisonWell]] = {}
        for well in selected:
            selected_by_plate.setdefault(well.plate_id, []).append(well)
        if len(selected_by_plate) < 2:
            raise ValueError("Choose wells from at least two plates for Growth comparison")

        plates_by_id = {plate.plate_id: plate for plate in plates}
        points: list[GrowthPlotPoint] = []
        issues: list[DomainIssue] = []
        cache_inputs: list[dict[str, object]] = []
        for plate in plates:
            selected_for_plate = selected_by_plate.get(plate.plate_id)
            if not selected_for_plate:
                continue
            plate_id = PlateId(plate.plate_id)
            snapshot = self.repository.load_plate(plate_id)
            if snapshot is None:
                raise LookupError(f"Growth plate not found: {plate.plate_id}")
            if str(snapshot.metadata.get("assay_type", "")) != AssayType.GROWTH:
                raise ValueError(f"Plate is not a growth run: {plate.plate_id}")
            token = self.repository.plate_cache_token(plate_id)
            if token is None or not token.strip():
                raise LookupError(f"Growth plate cache token not found: {plate.plate_id}")

            snapshot_wells = {str(well["well_id"]): well for well in snapshot.wells}
            missing = tuple(
                well.well_id for well in selected_for_plate if well.well_id not in snapshot_wells
            )
            if missing:
                raise ValueError(
                    f"Growth comparison wells are not present in plate {plate.plate_id}: "
                    + ", ".join(missing)
                )
            changed_positions = tuple(
                well.well_id
                for well in selected_for_plate
                if str(snapshot_wells[well.well_id].get("position", "")).strip() != well.position
            )
            if changed_positions:
                raise ValueError(
                    f"Growth comparison well positions changed in plate {plate.plate_id}: "
                    + ", ".join(changed_positions)
                )
            positions = tuple(well.position for well in selected_for_plate)
            prepared = self.plot_preparer.execute(snapshot, (), positions, corrected=False)
            issues.extend(prepared.issues)
            selected_by_position = {well.position: well for well in selected_for_plate}
            points.extend(
                _comparison_plot_point(
                    point, plates_by_id[plate.plate_id], selected_by_position[point.position]
                )
                for point in prepared.points
            )
            cache_inputs.append(
                {
                    "plate_id": plate.plate_id,
                    "token": token,
                    "wells": tuple(_well_cache_value(well) for well in selected_for_plate),
                }
            )

        return GrowthComparisonPlotResult(
            plot_data=GrowthPlotData(tuple(points), tuple(issues), False),
            cache_key=_comparison_cache_key(cache_inputs),
            plate_count=len(selected_by_plate),
            well_count=len(selected),
        )


def _validated_requested_plate_ids(plate_ids: Sequence[PlateId]) -> tuple[PlateId, ...]:
    requested_ids = tuple(PlateId(str(plate_id).strip()) for plate_id in plate_ids)
    if len(requested_ids) < 2:
        raise ValueError("Choose at least two plates for Growth comparison")
    if any(not plate_id for plate_id in requested_ids):
        raise ValueError("Growth comparison plate IDs cannot be empty")
    if len(set(requested_ids)) != len(requested_ids):
        raise ValueError("Growth comparison plate IDs must be unique")
    return requested_ids


def _validated_plate_index(
    plates: Sequence[GrowthComparisonPlate], *, require_two: bool
) -> tuple[GrowthComparisonPlate, ...]:
    index = tuple(plates)
    if require_two and len(index) < 2:
        raise ValueError("Choose at least two plates for Growth comparison")
    if any(not isinstance(plate, GrowthComparisonPlate) for plate in index):
        raise ValueError("Growth comparison index contains an invalid plate")
    plate_ids = tuple(plate.plate_id for plate in index)
    if len(set(plate_ids)) != len(plate_ids):
        raise ValueError("Growth comparison plate IDs must be unique")
    return index


def _plates_from_well_rows(
    requested_ids: tuple[PlateId, ...], rows: Sequence[Mapping[str, object]]
) -> tuple[GrowthComparisonPlate, ...]:
    requested_by_text = {str(plate_id): plate_id for plate_id in requested_ids}
    wells_by_plate: dict[str, list[GrowthComparisonWell]] = {
        str(plate_id): [] for plate_id in requested_ids
    }
    names_by_plate: dict[str, tuple[str | None, str | None] | None] = {
        str(plate_id): None for plate_id in requested_ids
    }
    for row in rows:
        plate_id = _required_row_text(row, "plate_id")
        if plate_id not in requested_by_text:
            raise ValueError(f"Growth comparison query returned unexpected plate: {plate_id}")
        names = (_optional_row_text(row, "experiment_name"), _optional_row_text(row, "plate_name"))
        previous_names = names_by_plate[plate_id]
        if previous_names is not None and names != previous_names:
            raise ValueError(
                f"Growth comparison query returned inconsistent names for plate: {plate_id}"
            )
        names_by_plate[plate_id] = names
        wells_by_plate[plate_id].append(_well_from_row(row, plate_id))

    missing = tuple(plate_id for plate_id in requested_ids if not wells_by_plate[str(plate_id)])
    if missing:
        raise ValueError(
            "Growth comparison query did not return requested plates: "
            + ", ".join(str(plate_id) for plate_id in missing)
        )
    return tuple(
        GrowthComparisonPlate(
            plate_id=str(plate_id),
            wells=tuple(wells_by_plate[str(plate_id)]),
            experiment_name=names_by_plate[str(plate_id)][0],  # type: ignore[index]
            plate_name=names_by_plate[str(plate_id)][1],  # type: ignore[index]
        )
        for plate_id in requested_ids
    )


def _well_from_row(row: Mapping[str, object], plate_id: str) -> GrowthComparisonWell:
    return GrowthComparisonWell(
        plate_id=plate_id,
        well_id=_required_row_text(row, "well_id"),
        position=_required_row_text(row, "position"),
        display_name=_optional_row_text(row, "display_name"),
        strain=_optional_row_text(row, "strain"),
        treatment=_optional_row_text(row, "treatment"),
        concentration=_optional_row_number(row, "concentration"),
        concentration_unit=_optional_row_text(row, "concentration_unit"),
        medium=_optional_row_text(row, "medium"),
        replicate=_optional_row_replicate(row),
        grouping_label=_optional_row_text(row, "grouping_label"),
        inoculum_size=_optional_row_number(row, "inoculum_size"),
        inoculum_unit=_optional_row_text(row, "inoculum_unit"),
        is_blank=_row_bool(row, "is_blank"),
    )


def _required_row_text(row: Mapping[str, object], name: str) -> str:
    value = _optional_row_text(row, name)
    if value is None:
        raise ValueError(f"Growth comparison row is missing {name}")
    return value


def _optional_row_text(row: Mapping[str, object], name: str) -> str | None:
    return _trimmed_text(row.get(name))


def _optional_row_number(row: Mapping[str, object], name: str) -> int | float | Decimal | None:
    value = row.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as error:
            raise ValueError(f"Growth comparison row has invalid {name}") from error
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ValueError(f"Growth comparison row has invalid {name}")
    return value


def _optional_row_replicate(row: Mapping[str, object]) -> int | None:
    value = row.get("replicate")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("Growth comparison row has invalid replicate")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as error:
            raise ValueError("Growth comparison row has invalid replicate") from error
    raise ValueError("Growth comparison row has invalid replicate")


def _row_bool(row: Mapping[str, object], name: str) -> bool:
    value = row.get(name)
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError(f"Growth comparison row has invalid {name}")


def _matches_filter(well: GrowthComparisonWell, filters: GrowthWellSearchFilter) -> bool:
    if well.is_blank and not filters.include_blank_wells:
        return False
    if filters.text is not None:
        text = filters.text.casefold()
        searchable = (
            well.position,
            well.display_name,
            well.strain,
            well.treatment,
            well.concentration_unit,
            well.medium,
            well.grouping_label,
            well.inoculum_unit,
        )
        if not any(text in value.casefold() for value in searchable if value is not None):
            return False
    if not _matches_any_text(well.strain, filters.strains):
        return False
    if not _matches_any_text(well.treatment, filters.treatments):
        return False
    if not _matches_any_text(well.concentration_unit, filters.concentration_units):
        return False
    if not _matches_any_text(well.medium, filters.media):
        return False
    if not _matches_any_text(well.grouping_label, filters.grouping_labels):
        return False
    if not _matches_any_text(well.inoculum_unit, filters.inoculum_units):
        return False
    if filters.replicates and well.replicate not in filters.replicates:
        return False
    if filters.inoculum_sizes:
        if well.inoculum_size is None:
            return False
        inoculum_size = _normalized_number(well.inoculum_size, "inoculum size")
        if inoculum_size not in filters.inoculum_sizes:
            return False
    if filters.concentration_min is not None or filters.concentration_max is not None:
        if well.concentration is None:
            return False
        concentration = _normalized_number(well.concentration, "concentration")
        if filters.concentration_min is not None and concentration < filters.concentration_min:
            return False
        if filters.concentration_max is not None and concentration > filters.concentration_max:
            return False
    return True


def _growth_comparison_quick_stats(
    wells: Sequence[GrowthComparisonWell], field_keys: Sequence[str]
) -> GrowthComparisonQuickStats:
    keys = tuple(str(key).strip() for key in field_keys)
    if any(not key for key in keys):
        raise ValueError("Growth comparison quick-stat fields cannot be empty")
    if len(set(keys)) != len(keys):
        raise ValueError("Growth comparison quick-stat fields must be unique")
    unknown = tuple(key for key in keys if key not in _SUMMARY_FIELDS_BY_KEY)
    if unknown:
        raise ValueError("Unknown Growth comparison quick-stat field: " + ", ".join(unknown))

    fields = tuple(_SUMMARY_FIELDS_BY_KEY[key] for key in keys)
    grouped: dict[tuple[str, ...], tuple[tuple[str, ...], int, set[str]]] = {}
    for well in wells:
        values = tuple(_summary_value(well, key) for key in keys)
        identity = tuple(value.casefold() for value in values)
        existing = grouped.get(identity)
        if existing is None:
            grouped[identity] = (values, 1, {well.plate_id})
        else:
            display_values, well_count, plate_ids = existing
            plate_ids.add(well.plate_id)
            grouped[identity] = (display_values, well_count + 1, plate_ids)
    groups = tuple(
        GrowthComparisonQuickStat(values, well_count, len(plate_ids))
        for values, well_count, plate_ids in sorted(
            grouped.values(), key=lambda item: tuple(value.casefold() for value in item[0])
        )
    )
    return GrowthComparisonQuickStats(fields, groups, len(wells))


def _summary_value(well: GrowthComparisonWell, key: str) -> str:
    if key == "concentration":
        return _quantity_label(well.concentration, well.concentration_unit)
    if key == "inoculum_size":
        return _quantity_label(well.inoculum_size, well.inoculum_unit)
    if key == "is_blank":
        return "Blank" if well.is_blank else "Sample"
    value = getattr(well, key)
    return str(value).strip() if value is not None and str(value).strip() else "—"


def _quantity_label(value: int | float | Decimal | None, unit: str | None) -> str:
    if value is None:
        return "—"
    number = _format_number(value)
    return f"{number} {unit}" if unit else number


def _matches_any_text(value: str | None, allowed: tuple[str, ...]) -> bool:
    if not allowed:
        return True
    if value is None:
        return False
    normalized = value.casefold()
    return any(normalized == candidate.casefold() for candidate in allowed)


def _validated_selected_wells(
    plates: tuple[GrowthComparisonPlate, ...], selected_wells: Sequence[GrowthComparisonWell]
) -> tuple[GrowthComparisonWell, ...]:
    selected = tuple(selected_wells)
    if not selected:
        raise ValueError("Choose at least one Growth well to render")
    if any(not isinstance(well, GrowthComparisonWell) for well in selected):
        raise ValueError("Selected Growth comparison well has an invalid type")
    known = {(plate.plate_id, well.well_id): well for plate in plates for well in plate.wells}
    identity = tuple((well.plate_id, well.well_id) for well in selected)
    if len(set(identity)) != len(identity):
        raise ValueError("Selected Growth comparison wells must be unique")
    unknown = tuple(key for key in identity if key not in known)
    if unknown:
        raise ValueError(
            "Selected Growth comparison well does not belong to the supplied plate index"
        )
    return tuple(known[key] for key in identity)


def _comparison_plot_point(
    point: GrowthPlotPoint, plate: GrowthComparisonPlate, well: GrowthComparisonWell
) -> GrowthPlotPoint:
    experiment = plate.experiment_name or "Unnamed experiment"
    plate_name = plate.plate_name or plate.plate_id
    return GrowthPlotPoint(
        position=f"{well.plate_id}:{well.well_id}",
        label=" | ".join((experiment, plate_name, well.position, _well_label(well))),
        elapsed_minutes=point.elapsed_minutes,
        channel=point.channel,
        value=point.value,
        value_raw=point.value_raw,
        background_mean=point.background_mean,
        correction_applied=point.correction_applied,
        time_index=point.time_index,
        elapsed_microseconds=point.elapsed_microseconds,
    )


def _well_label(well: GrowthComparisonWell) -> str:
    concentration = (
        " ".join(
            value
            for value in (
                _format_number(well.concentration) if well.concentration is not None else None,
                well.concentration_unit,
            )
            if value
        )
        or None
    )
    inoculum = (
        _quantity_label(well.inoculum_size, well.inoculum_unit)
        if well.inoculum_size is not None
        else None
    )
    replicate = f"replicate {well.replicate}" if well.replicate is not None else None
    return (
        "; ".join(
            value
            for value in (
                well.display_name,
                well.strain,
                well.treatment,
                concentration,
                well.medium,
                inoculum,
                well.grouping_label,
                replicate,
            )
            if value
        )
        or well.well_id
    )


def _well_cache_value(well: GrowthComparisonWell) -> tuple[object, ...]:
    return (
        well.well_id,
        well.position,
        well.display_name,
        well.strain,
        well.treatment,
        str(_normalized_number(well.concentration, "concentration"))
        if well.concentration is not None
        else None,
        well.concentration_unit,
        well.medium,
        well.replicate,
        well.grouping_label,
        str(_normalized_number(well.inoculum_size, "inoculum size"))
        if well.inoculum_size is not None
        else None,
        well.inoculum_unit,
        well.is_blank,
    )


def _comparison_cache_key(cache_inputs: Sequence[Mapping[str, object]]) -> str:
    encoded = json.dumps(
        tuple(cache_inputs), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return f"growth-comparison:{sha256(encoded).hexdigest()}"


def _trimmed_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _unique_text_values(values: Sequence[object]) -> tuple[str, ...]:
    normalized: dict[str, str] = {}
    for value in values:
        text = _trimmed_text(value)
        if text is None:
            raise ValueError("Growth comparison filter values cannot be empty")
        normalized.setdefault(text.casefold(), text)
    return tuple(normalized[key] for key in sorted(normalized))


def _normalized_number(value: int | float | Decimal, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Real | Decimal):
        raise ValueError(f"Growth comparison {name} must be a finite number")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Growth comparison {name} must be a finite number") from error
    if not decimal.is_finite() or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"Growth comparison {name} must be a finite number")
    return decimal.normalize()


def _format_number(value: int | float | Decimal) -> str:
    return format(_normalized_number(value, "number"), "f")


_POSITION_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)$")
_SEARCH_RESULT_LIMIT = 500


def _well_sort_key(well: GrowthComparisonWell) -> tuple[int, int, int, str, str]:
    match = _POSITION_PATTERN.fullmatch(well.position)
    if match:
        row = sum(
            (ord(letter) - ord("A") + 1) * (26**index)
            for index, letter in enumerate(reversed(match[1].upper()))
        )
        return (0, row, int(match[2]), "", well.well_id)
    return (1, 0, 0, well.position.casefold(), well.well_id)
