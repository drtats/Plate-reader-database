"""Pure condition matching for cross-plate Growth comparisons.

The matching key deliberately contains normalized metadata only.  The original,
trimmed values remain on :class:`GrowthComparisonWell` and a readable condition
display accompanies each match, so case-insensitive matching never forces the UI
to show normalized (case-folded) scientific labels.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
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


class GrowthComparisonMatchField(StrEnum):
    """Metadata fields that may define a cross-plate Growth condition."""

    STRAIN = "strain"
    TREATMENT = "treatment"
    CONCENTRATION = "concentration"
    MEDIUM = "medium"


DEFAULT_GROWTH_COMPARISON_FIELDS = (
    GrowthComparisonMatchField.STRAIN,
    GrowthComparisonMatchField.TREATMENT,
    GrowthComparisonMatchField.CONCENTRATION,
)


@dataclass(frozen=True, slots=True)
class GrowthComparisonWell:
    """A single well's condition data, without measurements or UI concerns."""

    plate_id: str
    well_id: str
    position: str
    strain: str | None = None
    treatment: str | None = None
    concentration: int | float | Decimal | None = None
    concentration_unit: str | None = None
    medium: str | None = None
    replicate: int | None = None
    is_blank: bool = False

    def __post_init__(self) -> None:
        for name in ("plate_id", "well_id", "position"):
            value = getattr(self, name).strip()
            if not value:
                raise ValueError(f"Growth comparison well {name} cannot be empty")
            object.__setattr__(self, name, value)
        for name in ("strain", "treatment", "concentration_unit", "medium"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, str(value).strip() or None)
        if self.replicate is not None and (
            isinstance(self.replicate, bool)
            or not isinstance(self.replicate, int)
            or self.replicate < 1
        ):
            raise ValueError("Growth comparison replicate must be a positive integer when present")
        if self.concentration is not None:
            _normalized_concentration(self.concentration)


@dataclass(frozen=True, slots=True)
class GrowthComparisonPlate:
    """The complete condition-only index for one selected plate."""

    plate_id: str
    wells: tuple[GrowthComparisonWell, ...]
    experiment_name: str | None = None
    plate_name: str | None = None

    def __post_init__(self) -> None:
        plate_id = self.plate_id.strip()
        if not plate_id:
            raise ValueError("Growth comparison plate_id cannot be empty")
        wells = tuple(self.wells)
        if any(well.plate_id != plate_id for well in wells):
            raise ValueError("Every Growth comparison well must belong to its containing plate")
        well_ids = [well.well_id for well in wells]
        if len(set(well_ids)) != len(well_ids):
            raise ValueError("Growth comparison plate contains duplicate well_id values")
        object.__setattr__(self, "plate_id", plate_id)
        object.__setattr__(self, "wells", wells)
        for name in ("experiment_name", "plate_name"):
            value = getattr(self, name)
            object.__setattr__(
                self, name, str(value).strip() or None if value is not None else None
            )


@dataclass(frozen=True, slots=True)
class GrowthConditionKey:
    """Case-folded, selected metadata used as an exact intersection key."""

    strain: str | None = None
    treatment: str | None = None
    concentration: Decimal | None = None
    concentration_unit: str | None = None
    medium: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthConditionDisplay:
    """A readable representative label for a normalized condition key."""

    strain: str | None = None
    treatment: str | None = None
    concentration: str | None = None
    concentration_unit: str | None = None
    medium: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthComparisonPlateMatch:
    """All replicate wells from one plate that share a matched condition."""

    plate_id: str
    wells: tuple[GrowthComparisonWell, ...]


@dataclass(frozen=True, slots=True)
class GrowthComparisonMatch:
    """One condition occurring in every selected plate."""

    condition: GrowthConditionKey
    display: GrowthConditionDisplay
    plate_matches: tuple[GrowthComparisonPlateMatch, ...]


@dataclass(frozen=True, slots=True)
class GrowthComparisonExclusions:
    """Condition-only discovery exclusions for a selected plate."""

    plate_id: str
    blank_well_count: int
    missing_required_metadata_count: int

    @property
    def excluded_well_count(self) -> int:
        return self.blank_well_count + self.missing_required_metadata_count


@dataclass(frozen=True, slots=True)
class GrowthComparisonResult:
    """Common conditions plus transparent per-plate exclusion information."""

    match_fields: tuple[GrowthComparisonMatchField, ...]
    matches: tuple[GrowthComparisonMatch, ...]
    exclusions: tuple[GrowthComparisonExclusions, ...]


@dataclass(frozen=True, slots=True)
class GrowthComparisonPlotResult:
    """Raw comparison plot data loaded only after an explicit render request."""

    plot_data: GrowthPlotData
    cache_key: str
    plate_count: int
    well_count: int


class GrowthComparisonConditionsRepository(Protocol):
    """Authorized condition-only read surface used before curve rendering."""

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def growth_comparison_wells(
        self, plate_ids: Sequence[PlateId]
    ) -> Sequence[Mapping[str, object]]:
        """Return requested wells and condition metadata, never measurements."""


class LoadGrowthComparisonConditionsService:
    """Load selected Growth plate conditions in one authorized repository call."""

    def __init__(self, repository: GrowthComparisonConditionsRepository) -> None:
        self.repository = repository

    def execute(
        self, actor: Actor, plate_ids: Sequence[PlateId]
    ) -> tuple[GrowthComparisonPlate, ...]:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        requested_ids = _validated_requested_plate_ids(plate_ids)
        rows = self.repository.growth_comparison_wells(requested_ids)
        return _plates_from_condition_rows(requested_ids, rows)


class GrowthComparisonPlotRepository(Protocol):
    """Minimal authorized raw-data surface for an explicit comparison render."""

    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...

    def plate_cache_token(self, plate_id: PlateId) -> str | None: ...


class LoadGrowthComparisonPlotService:
    """Load raw observations for selected common conditions and combine plot points."""

    def __init__(self, repository: GrowthComparisonPlotRepository) -> None:
        self.repository = repository
        self.plot_preparer = PrepareGrowthPlotDataService()

    def execute(
        self,
        actor: Actor,
        plates: Sequence[GrowthComparisonPlate],
        selected_matches: Sequence[GrowthComparisonMatch],
    ) -> GrowthComparisonPlotResult:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        selected_plates = tuple(plates)
        _validate_plates(selected_plates)
        positions_by_plate, displays_by_plate = _selected_plot_membership(
            selected_plates, selected_matches
        )

        points: list[GrowthPlotPoint] = []
        issues: list[DomainIssue] = []
        cache_inputs: list[dict[str, object]] = []
        for plate in selected_plates:
            plate_id = PlateId(plate.plate_id)
            snapshot = self.repository.load_plate(plate_id)
            if snapshot is None:
                raise LookupError(f"Growth plate not found: {plate.plate_id}")
            if str(snapshot.metadata.get("assay_type", "")) != AssayType.GROWTH:
                raise ValueError(f"Plate is not a growth run: {plate.plate_id}")
            token = self.repository.plate_cache_token(plate_id)
            if token is None or not token.strip():
                raise LookupError(f"Growth plate cache token not found: {plate.plate_id}")

            positions = positions_by_plate[plate.plate_id]
            snapshot_positions = {str(well["position"]) for well in snapshot.wells}
            missing_positions = tuple(
                position for position in positions if position not in snapshot_positions
            )
            if missing_positions:
                formatted_positions = ", ".join(missing_positions)
                raise ValueError(
                    f"Growth comparison positions are not present in plate {plate.plate_id}: "
                    f"{formatted_positions}"
                )
            prepared = self.plot_preparer.execute(snapshot, (), positions, corrected=False)
            issues.extend(prepared.issues)
            points.extend(
                _comparison_plot_point(
                    point,
                    plate,
                    displays_by_plate[plate.plate_id][point.position],
                )
                for point in prepared.points
            )
            cache_inputs.append(
                {
                    "plate_id": plate.plate_id,
                    "token": token,
                    "positions": positions,
                    "conditions": tuple(
                        _display_cache_value(displays_by_plate[plate.plate_id][position])
                        for position in positions
                    ),
                }
            )

        return GrowthComparisonPlotResult(
            plot_data=GrowthPlotData(tuple(points), tuple(issues), False),
            cache_key=_comparison_cache_key(cache_inputs),
            plate_count=len(selected_plates),
            well_count=sum(len(positions) for positions in positions_by_plate.values()),
        )


class FindCommonGrowthConditionsService:
    """Find exact, normalized condition intersections without loading measurements."""

    def execute(
        self,
        plates: Sequence[GrowthComparisonPlate],
        match_fields: Iterable[GrowthComparisonMatchField] = DEFAULT_GROWTH_COMPARISON_FIELDS,
    ) -> GrowthComparisonResult:
        fields = _validated_match_fields(match_fields)
        selected_plates = tuple(plates)
        _validate_plates(selected_plates)

        indexed_wells: dict[GrowthConditionKey, dict[str, list[GrowthComparisonWell]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        displays: dict[GrowthConditionKey, list[GrowthConditionDisplay]] = defaultdict(list)
        exclusions: list[GrowthComparisonExclusions] = []

        for plate in selected_plates:
            blank_count = 0
            missing_count = 0
            for well in plate.wells:
                if well.is_blank:
                    blank_count += 1
                    continue
                condition = _condition_for(well, fields)
                if condition is None:
                    missing_count += 1
                    continue
                key, display = condition
                indexed_wells[key][plate.plate_id].append(well)
                displays[key].append(display)
            exclusions.append(
                GrowthComparisonExclusions(
                    plate_id=plate.plate_id,
                    blank_well_count=blank_count,
                    missing_required_metadata_count=missing_count,
                )
            )

        ordered_plate_ids = tuple(plate.plate_id for plate in selected_plates)
        matches = tuple(
            GrowthComparisonMatch(
                condition=key,
                display=_representative_display(displays[key]),
                plate_matches=tuple(
                    GrowthComparisonPlateMatch(
                        plate_id=plate_id,
                        wells=tuple(sorted(by_plate[plate_id], key=_well_sort_key)),
                    )
                    for plate_id in ordered_plate_ids
                ),
            )
            for key, by_plate in sorted(
                indexed_wells.items(), key=lambda item: _condition_sort_key(item[0])
            )
            if all(plate_id in by_plate for plate_id in ordered_plate_ids)
        )
        return GrowthComparisonResult(fields, matches, tuple(exclusions))


def find_common_growth_conditions(
    plates: Sequence[GrowthComparisonPlate],
    match_fields: Iterable[GrowthComparisonMatchField] = DEFAULT_GROWTH_COMPARISON_FIELDS,
) -> GrowthComparisonResult:
    """Convenience function for :class:`FindCommonGrowthConditionsService`."""

    return FindCommonGrowthConditionsService().execute(plates, match_fields)


def _validated_requested_plate_ids(plate_ids: Sequence[PlateId]) -> tuple[PlateId, ...]:
    requested_ids = tuple(PlateId(str(plate_id).strip()) for plate_id in plate_ids)
    if len(requested_ids) < 2:
        raise ValueError("Choose at least two plates for Growth comparison")
    if any(not plate_id for plate_id in requested_ids):
        raise ValueError("Growth comparison plate IDs cannot be empty")
    if len(set(requested_ids)) != len(requested_ids):
        raise ValueError("Growth comparison plate IDs must be unique")
    return requested_ids


def _plates_from_condition_rows(
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
        wells_by_plate[plate_id].append(_well_from_condition_row(row, plate_id))

    missing = tuple(plate_id for plate_id in requested_ids if not wells_by_plate[str(plate_id)])
    if missing:
        missing_ids = ", ".join(str(plate_id) for plate_id in missing)
        raise ValueError(f"Growth comparison query did not return requested plates: {missing_ids}")
    plates: list[GrowthComparisonPlate] = []
    for plate_id in requested_ids:
        plate_names = names_by_plate[str(plate_id)]
        assert plate_names is not None  # A row exists for every requested plate above.
        plates.append(
            GrowthComparisonPlate(
                plate_id=str(plate_id),
                wells=tuple(wells_by_plate[str(plate_id)]),
                experiment_name=plate_names[0],
                plate_name=plate_names[1],
            )
        )
    return tuple(plates)


def _well_from_condition_row(row: Mapping[str, object], plate_id: str) -> GrowthComparisonWell:
    return GrowthComparisonWell(
        plate_id=plate_id,
        well_id=_required_row_text(row, "well_id"),
        position=_required_row_text(row, "position"),
        strain=_optional_row_text(row, "strain"),
        treatment=_optional_row_text(row, "treatment"),
        concentration=_optional_row_concentration(row),
        concentration_unit=_optional_row_text(row, "concentration_unit"),
        medium=_optional_row_text(row, "medium"),
        replicate=_optional_row_replicate(row),
        is_blank=_row_bool(row, "is_blank"),
    )


def _required_row_text(row: Mapping[str, object], name: str) -> str:
    value = _optional_row_text(row, name)
    if value is None:
        raise ValueError(f"Growth comparison row is missing {name}")
    return value


def _optional_row_text(row: Mapping[str, object], name: str) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_row_concentration(row: Mapping[str, object]) -> int | float | Decimal | None:
    value = row.get("concentration")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as error:
            raise ValueError("Growth comparison row has invalid concentration") from error
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ValueError("Growth comparison row has invalid concentration")
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


def _selected_plot_membership(
    plates: tuple[GrowthComparisonPlate, ...],
    selected_matches: Sequence[GrowthComparisonMatch],
) -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, GrowthConditionDisplay]]]:
    if not selected_matches:
        raise ValueError("Choose at least one common Growth condition to render")
    expected_plate_ids = tuple(plate.plate_id for plate in plates)
    expected_plate_set = set(expected_plate_ids)
    known_wells = {
        plate.plate_id: {well.position: well for well in plate.wells} for plate in plates
    }
    positions: dict[str, set[str]] = {plate_id: set() for plate_id in expected_plate_ids}
    displays: dict[str, dict[str, GrowthConditionDisplay]] = {
        plate_id: {} for plate_id in expected_plate_ids
    }
    for match in selected_matches:
        if not isinstance(match, GrowthComparisonMatch):
            raise ValueError("Selected Growth comparison condition has an invalid type")
        plate_matches = tuple(match.plate_matches)
        match_plate_ids = tuple(plate_match.plate_id for plate_match in plate_matches)
        if (
            len(set(match_plate_ids)) != len(match_plate_ids)
            or set(match_plate_ids) != expected_plate_set
        ):
            raise ValueError(
                "Selected Growth comparison condition does not belong to the supplied plates"
            )
        for plate_match in plate_matches:
            if not plate_match.wells:
                raise ValueError("Selected Growth comparison condition has no wells for a plate")
            known_plate_wells = known_wells[plate_match.plate_id]
            for well in plate_match.wells:
                known = known_plate_wells.get(well.position)
                if known != well:
                    raise ValueError(
                        "Selected Growth comparison well does not belong to the supplied plate"
                    )
                existing_display = displays[plate_match.plate_id].get(well.position)
                if existing_display is not None and existing_display != match.display:
                    raise ValueError(
                        "Selected Growth comparison conditions assign conflicting settings "
                        "to a well"
                    )
                positions[plate_match.plate_id].add(well.position)
                displays[plate_match.plate_id][well.position] = match.display

    ordered_positions = {
        plate.plate_id: tuple(
            well.position for well in plate.wells if well.position in positions[plate.plate_id]
        )
        for plate in plates
    }
    if any(not values for values in ordered_positions.values()):  # Defensive membership guard.
        raise ValueError(
            "Selected Growth comparison condition does not include every supplied plate"
        )
    return ordered_positions, displays


def _comparison_plot_point(
    point: GrowthPlotPoint,
    plate: GrowthComparisonPlate,
    condition: GrowthConditionDisplay,
) -> GrowthPlotPoint:
    experiment = plate.experiment_name or "Unnamed experiment"
    plate_name = plate.plate_name or plate.plate_id
    condition_label = _condition_label(condition)
    label_parts = (experiment, plate_name, point.position, condition_label)
    return GrowthPlotPoint(
        position=f"{plate.plate_id}:{point.position}",
        label=" | ".join(part for part in label_parts if part),
        elapsed_minutes=point.elapsed_minutes,
        channel=point.channel,
        value=point.value,
        value_raw=point.value_raw,
        background_mean=point.background_mean,
        correction_applied=point.correction_applied,
        time_index=point.time_index,
        elapsed_microseconds=point.elapsed_microseconds,
    )


def _condition_label(condition: GrowthConditionDisplay) -> str:
    concentration = " ".join(
        value
        for value in (condition.concentration, condition.concentration_unit)
        if value is not None
    )
    return "; ".join(
        value
        for value in (condition.strain, condition.treatment, concentration, condition.medium)
        if value
    )


def _display_cache_value(condition: GrowthConditionDisplay) -> tuple[str | None, ...]:
    return (
        condition.strain,
        condition.treatment,
        condition.concentration,
        condition.concentration_unit,
        condition.medium,
    )


def _comparison_cache_key(cache_inputs: Sequence[Mapping[str, object]]) -> str:
    encoded = json.dumps(
        tuple(cache_inputs), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return f"growth-comparison:{sha256(encoded).hexdigest()}"


def _validated_match_fields(
    match_fields: Iterable[GrowthComparisonMatchField],
) -> tuple[GrowthComparisonMatchField, ...]:
    fields = tuple(match_fields)
    if not fields:
        raise ValueError("Choose at least one Growth comparison match field")
    if any(not isinstance(field, GrowthComparisonMatchField) for field in fields):
        raise ValueError("Growth comparison match fields must be GrowthComparisonMatchField values")
    if len(set(fields)) != len(fields):
        raise ValueError("Growth comparison match fields cannot be repeated")
    return fields


def _validate_plates(plates: Sequence[GrowthComparisonPlate]) -> None:
    if len(plates) < 2:
        raise ValueError("Choose at least two plates for Growth comparison")
    plate_ids = tuple(plate.plate_id for plate in plates)
    if len(set(plate_ids)) != len(plate_ids):
        raise ValueError("Growth comparison plate IDs must be unique")


def _condition_for(
    well: GrowthComparisonWell,
    fields: tuple[GrowthComparisonMatchField, ...],
) -> tuple[GrowthConditionKey, GrowthConditionDisplay] | None:
    strain = treatment = medium = None
    concentration = concentration_unit = None
    display_strain = display_treatment = display_medium = None
    display_concentration = display_concentration_unit = None

    if GrowthComparisonMatchField.STRAIN in fields:
        value = _normalized_text(well.strain)
        if value is None:
            return None
        strain, display_strain = value
    if GrowthComparisonMatchField.TREATMENT in fields:
        value = _normalized_text(well.treatment)
        if value is None:
            return None
        treatment, display_treatment = value
    if GrowthComparisonMatchField.CONCENTRATION in fields:
        if well.concentration is None:
            return None
        unit = _normalized_text(well.concentration_unit)
        if unit is None:
            return None
        concentration = _normalized_concentration(well.concentration)
        concentration_unit, display_concentration_unit = unit
        display_concentration = _format_concentration(concentration)
    if GrowthComparisonMatchField.MEDIUM in fields:
        value = _normalized_text(well.medium)
        if value is None:
            return None
        medium, display_medium = value

    return (
        GrowthConditionKey(strain, treatment, concentration, concentration_unit, medium),
        GrowthConditionDisplay(
            display_strain,
            display_treatment,
            display_concentration,
            display_concentration_unit,
            display_medium,
        ),
    )


def _normalized_text(value: str | None) -> tuple[str, str] | None:
    display = (value or "").strip()
    return (display.casefold(), display) if display else None


def _normalized_concentration(value: int | float | Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Real | Decimal):
        raise ValueError("Growth comparison concentration must be a finite number")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Growth comparison concentration must be a finite number") from error
    if not decimal.is_finite() or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("Growth comparison concentration must be a finite number")
    return decimal.normalize()


def _format_concentration(value: Decimal) -> str:
    return format(value, "f")


def _representative_display(
    displays: Sequence[GrowthConditionDisplay],
) -> GrowthConditionDisplay:
    return min(
        displays,
        key=lambda display: (
            display.strain or "",
            display.treatment or "",
            display.concentration or "",
            display.concentration_unit or "",
            display.medium or "",
        ),
    )


def _condition_sort_key(key: GrowthConditionKey) -> tuple[str, str, Decimal, str, str]:
    return (
        key.strain or "",
        key.treatment or "",
        key.concentration if key.concentration is not None else Decimal("-Infinity"),
        key.concentration_unit or "",
        key.medium or "",
    )


_POSITION_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)$")


def _well_sort_key(well: GrowthComparisonWell) -> tuple[int, int, int, str, str]:
    match = _POSITION_PATTERN.fullmatch(well.position)
    if match:
        row = sum(
            (ord(letter) - ord("A") + 1) * (26**index)
            for index, letter in enumerate(reversed(match[1].upper()))
        )
        return (well.replicate or 0, 0, row * 10_000 + int(match[2]), "", well.well_id)
    return (well.replicate or 0, 1, 0, well.position.casefold(), well.well_id)
