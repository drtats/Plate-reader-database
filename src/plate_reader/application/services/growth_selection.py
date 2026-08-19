"""Pure staged well-selection operations for Growth workflows."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from plate_reader.domain.common.plate import PLATE_96, WellPosition

_FIRST_CLASS_FIELDS = (
    ("display_name", "Display name"),
    ("raw_label", "Raw label"),
    ("strain", "Strain"),
    ("treatment", "Treatment"),
    ("concentration", "Concentration"),
    ("concentration_unit", "Concentration unit"),
    ("medium", "Media"),
    ("grouping_label", "Group"),
    ("replicate", "Replicate"),
)
_FIRST_CLASS_KEYS = {key for key, _label in _FIRST_CLASS_FIELDS}
_CUSTOM_PREFIX = "custom:"


class GrowthSelectionOperation(StrEnum):
    REPLACE = "replace"
    ADD = "add"
    REMOVE = "remove"
    KEEP_ONLY = "keep_only"


@dataclass(frozen=True, slots=True)
class GrowthWellFilter:
    field: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("Growth selection filter field cannot be empty")


@dataclass(frozen=True, slots=True)
class GrowthSelectionField:
    key: str
    label: str
    values: tuple[str, ...]


class GrowthWellSelectionService:
    """Filter a complete Growth layout and combine matches with staged positions."""

    def execute(
        self,
        wells: Sequence[Mapping[str, object]],
        current_positions: Iterable[str],
        filters: Sequence[GrowthWellFilter],
        operation: GrowthSelectionOperation,
    ) -> tuple[str, ...]:
        ordered_wells = _validated_wells(wells)
        current = _normalize_positions(current_positions)
        active_filters = tuple(item for item in filters if item.values)
        if not active_filters:
            return current

        available_keys = {field.key for field in growth_selection_fields(wells)}
        for item in active_filters:
            if item.field not in available_keys:
                raise ValueError(f"Unknown Growth selection field: {item.field}")

        matches = tuple(
            position
            for position, well in ordered_wells
            if all(_well_matches(well, item) for item in active_filters)
        )
        return combine_growth_selection(current, matches, operation)


def growth_selection_fields(
    wells: Sequence[Mapping[str, object]],
) -> tuple[GrowthSelectionField, ...]:
    """Return filterable fields and normalized values available in a layout."""

    ordered_wells = _validated_wells(wells)
    fields: list[GrowthSelectionField] = []
    for key, label in _FIRST_CLASS_FIELDS:
        values = _unique_values(well.get(key) for _position, well in ordered_wells)
        if values:
            fields.append(GrowthSelectionField(key, label, values))

    custom_names = sorted(
        {name for _position, well in ordered_wells for name in _custom_values(well)},
        key=str.casefold,
    )
    for name in custom_names:
        values = _unique_values(_custom_values(well).get(name) for _position, well in ordered_wells)
        if values:
            fields.append(
                GrowthSelectionField(f"{_CUSTOM_PREFIX}{name}", f"{name} (custom)", values)
            )
    return tuple(fields)


def normalize_growth_selection(
    wells: Sequence[Mapping[str, object]], positions: Iterable[str]
) -> tuple[str, ...]:
    """Validate a complete layout and return selected positions in physical order."""

    _validated_wells(wells)
    return _normalize_positions(positions)


def combine_growth_selection(
    current_positions: Iterable[str],
    matching_positions: Iterable[str],
    operation: GrowthSelectionOperation,
) -> tuple[str, ...]:
    """Apply one explicit set operation and return physical plate order."""

    current = set(_normalize_positions(current_positions))
    matches = set(_normalize_positions(matching_positions))
    if operation is GrowthSelectionOperation.REPLACE:
        combined = matches
    elif operation is GrowthSelectionOperation.ADD:
        combined = current | matches
    elif operation is GrowthSelectionOperation.REMOVE:
        combined = current - matches
    elif operation is GrowthSelectionOperation.KEEP_ONLY:
        combined = current & matches
    else:  # pragma: no cover - exhaustive StrEnum guard
        raise ValueError(f"Unsupported Growth selection operation: {operation}")
    return tuple(position.label for position in PLATE_96.positions() if position.label in combined)


def _validated_wells(
    wells: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, Mapping[str, object]], ...]:
    expected = tuple(position.label for position in PLATE_96.positions())
    by_position: dict[str, Mapping[str, object]] = {}
    for well in wells:
        raw_position = str(well.get("position", ""))
        position = WellPosition.parse(raw_position).label
        if position in by_position:
            raise ValueError(f"Duplicate Growth well position: {position}")
        by_position[position] = well
    if set(by_position) != set(expected):
        raise ValueError("Growth selection requires every A1-H12 well")
    return tuple((position, by_position[position]) for position in expected)


def _normalize_positions(positions: Iterable[str]) -> tuple[str, ...]:
    normalized = {WellPosition.parse(str(position)).label for position in positions}
    return tuple(
        position.label for position in PLATE_96.positions() if position.label in normalized
    )


def _well_matches(well: Mapping[str, object], item: GrowthWellFilter) -> bool:
    if item.field.startswith(_CUSTOM_PREFIX):
        value = _custom_values(well).get(item.field.removeprefix(_CUSTOM_PREFIX))
    elif item.field in _FIRST_CLASS_KEYS:
        value = well.get(item.field)
    else:
        raise ValueError(f"Unknown Growth selection field: {item.field}")
    expected = {_normalized_text(candidate) for candidate in item.values}
    return _normalized_text(value) in expected


def _custom_values(well: Mapping[str, object]) -> dict[str, object]:
    value = well.get("custom_json")
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, str):
        raise ValueError("Growth custom metadata must be a JSON object")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("Growth custom metadata must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("Growth custom metadata must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _unique_values(values: Iterable[object]) -> tuple[str, ...]:
    available: dict[str, str] = {}
    for value in values:
        text = _display_text(value)
        if text:
            available.setdefault(text.casefold(), text)
    return tuple(sorted(available.values(), key=_selection_sort_key))


def _display_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _normalized_text(value: object) -> str:
    return _display_text(value).casefold()


def _selection_sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value.casefold())
