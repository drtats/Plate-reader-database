"""Pure Growth display-name generation and layout CSV validation."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real

from plate_reader.domain.common.plate import PLATE_96, WellPosition

_CUSTOM_PREFIX = "custom:"


class GrowthDisplayNameScope(StrEnum):
    WELL = "well"
    PLATE = "plate"


class GrowthDisplayNumberFormat(StrEnum):
    GENERAL = "general"
    TWO_DECIMALS = "two_decimals"
    THREE_DECIMALS = "three_decimals"
    FOUR_DECIMALS = "four_decimals"


class GrowthDisplayNameAction(StrEnum):
    ADD = "add"
    CHANGE = "change"
    CLEAR = "clear"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class GrowthDisplayNameToken:
    scope: GrowthDisplayNameScope
    field: str

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("Display-name token field cannot be empty")


@dataclass(frozen=True, slots=True)
class GrowthDisplayNameOptions:
    tokens: tuple[GrowthDisplayNameToken, ...]
    separator: str = "_"
    prefix: str = ""
    suffix: str = ""
    omit_empty: bool = True
    number_format: GrowthDisplayNumberFormat = GrowthDisplayNumberFormat.GENERAL

    def __post_init__(self) -> None:
        if not self.tokens:
            raise ValueError("Choose at least one field for generated display names")
        identities = {(token.scope, token.field) for token in self.tokens}
        if len(identities) != len(self.tokens):
            raise ValueError("Display-name fields cannot be repeated")


@dataclass(frozen=True, slots=True)
class GrowthDisplayNameChange:
    position: str
    previous_name: str
    proposed_name: str
    action: GrowthDisplayNameAction


@dataclass(frozen=True, slots=True)
class GrowthDisplayNamePreview:
    changes: tuple[GrowthDisplayNameChange, ...]

    @property
    def changed_count(self) -> int:
        return sum(
            change.action is not GrowthDisplayNameAction.UNCHANGED for change in self.changes
        )

    @property
    def overwrite_count(self) -> int:
        return sum(
            change.action in {GrowthDisplayNameAction.CHANGE, GrowthDisplayNameAction.CLEAR}
            for change in self.changes
        )

    @property
    def clear_count(self) -> int:
        return sum(change.action is GrowthDisplayNameAction.CLEAR for change in self.changes)


class BuildGrowthDisplayNamesService:
    """Compose deterministic names for all or selected wells without persistence."""

    def execute(
        self,
        wells: Sequence[Mapping[str, object]],
        plate_metadata: Mapping[str, object],
        target_positions: Iterable[str],
        options: GrowthDisplayNameOptions,
    ) -> GrowthDisplayNamePreview:
        by_position = _validated_wells(wells)
        targets = set(_normalize_positions(target_positions))
        _validate_tokens(by_position, plate_metadata, options.tokens)
        changes = []
        for position in PLATE_96.positions():
            if position.label not in targets:
                continue
            well = by_position[position.label]
            proposed = _compose_display_name(well, plate_metadata, options)
            previous = str(well.get("display_name") or "").strip()
            changes.append(_change(position.label, previous, proposed))
        return GrowthDisplayNamePreview(tuple(changes))


def export_growth_display_name_csv(wells: Sequence[Mapping[str, object]]) -> bytes:
    """Return an Excel-friendly complete Well/Display name layout CSV."""

    by_position = _validated_wells(wells)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("Well", "Display name"))
    for position in PLATE_96.positions():
        writer.writerow(
            (position.label, str(by_position[position.label].get("display_name") or ""))
        )
    return stream.getvalue().encode("utf-8-sig")


def preview_growth_display_name_csv(
    wells: Sequence[Mapping[str, object]], content: bytes | str
) -> GrowthDisplayNamePreview:
    """Validate a complete or partial layout CSV and preview only listed wells."""

    by_position = _validated_wells(wells)
    text = _decode_csv(content)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader)
    except StopIteration as error:
        raise ValueError("Display-name CSV is empty") from error
    normalized_headers = [header.strip().casefold() for header in headers]
    if len(set(normalized_headers)) != len(normalized_headers):
        raise ValueError("Display-name CSV contains duplicate headers")
    required = {"well", "display name"}
    if not required.issubset(normalized_headers):
        raise ValueError("Display-name CSV requires Well and Display name headers")
    well_index = normalized_headers.index("well")
    name_index = normalized_headers.index("display name")

    proposed_by_position: dict[str, str] = {}
    for row_number, row in enumerate(reader, start=2):
        padded = (*row, *("" for _index in range(max(0, len(headers) - len(row)))))
        well_text = padded[well_index].strip()
        name = padded[name_index].strip()
        if not well_text and not name:
            continue
        if not well_text:
            raise ValueError(f"Display-name CSV row {row_number} is missing Well")
        try:
            position = WellPosition.parse(well_text).label
        except ValueError as error:
            raise ValueError(
                f"Display-name CSV row {row_number} has invalid well: {well_text}"
            ) from error
        if position in proposed_by_position:
            raise ValueError(f"Display-name CSV contains duplicate well: {position}")
        proposed_by_position[position] = name
    if not proposed_by_position:
        raise ValueError("Display-name CSV contains no well rows")

    changes = tuple(
        _change(
            position.label,
            str(by_position[position.label].get("display_name") or "").strip(),
            proposed_by_position[position.label],
        )
        for position in PLATE_96.positions()
        if position.label in proposed_by_position
    )
    return GrowthDisplayNamePreview(changes)


def _validated_wells(
    wells: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    expected = {position.label for position in PLATE_96.positions()}
    by_position: dict[str, Mapping[str, object]] = {}
    for well in wells:
        position = WellPosition.parse(str(well.get("position", ""))).label
        if position in by_position:
            raise ValueError(f"Duplicate Growth display-name well: {position}")
        by_position[position] = well
    if set(by_position) != expected:
        raise ValueError("Growth display names require every A1-H12 well")
    return by_position


def _normalize_positions(positions: Iterable[str]) -> tuple[str, ...]:
    normalized = {WellPosition.parse(str(position)).label for position in positions}
    return tuple(
        position.label for position in PLATE_96.positions() if position.label in normalized
    )


def _validate_tokens(
    wells: Mapping[str, Mapping[str, object]],
    plate_metadata: Mapping[str, object],
    tokens: Sequence[GrowthDisplayNameToken],
) -> None:
    available_well_fields = {"position"}
    for well in wells.values():
        available_well_fields.update(str(key) for key in well if key != "custom_fields")
        available_well_fields.update(f"{_CUSTOM_PREFIX}{key}" for key in _custom_fields(well))
    for token in tokens:
        if token.scope is GrowthDisplayNameScope.WELL and token.field not in available_well_fields:
            raise ValueError(f"Unknown Growth well display-name field: {token.field}")
        if token.scope is GrowthDisplayNameScope.PLATE and token.field not in plate_metadata:
            raise ValueError(f"Unknown Growth plate display-name field: {token.field}")


def _compose_display_name(
    well: Mapping[str, object],
    plate_metadata: Mapping[str, object],
    options: GrowthDisplayNameOptions,
) -> str:
    values = []
    for token in options.tokens:
        value = (
            _well_token_value(well, token.field)
            if token.scope is GrowthDisplayNameScope.WELL
            else plate_metadata.get(token.field)
        )
        text = _format_value(value, options.number_format)
        if text or not options.omit_empty:
            values.append(text)
    return f"{options.prefix}{options.separator.join(values)}{options.suffix}".strip()


def _well_token_value(well: Mapping[str, object], field: str) -> object:
    if field == "position":
        return well.get("position")
    if field.startswith(_CUSTOM_PREFIX):
        return _custom_fields(well).get(field.removeprefix(_CUSTOM_PREFIX))
    return well.get(field)


def _custom_fields(well: Mapping[str, object]) -> Mapping[str, object]:
    value = well.get("custom_fields")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Growth display-name custom fields must be a mapping")
    return {str(key): item for key, item in value.items()}


def _format_value(value: object, number_format: GrowthDisplayNumberFormat) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Real):
        numeric = float(value)
        if not math.isfinite(numeric):
            return ""
        if isinstance(value, int):
            return str(value)
        formats = {
            GrowthDisplayNumberFormat.GENERAL: ".6g",
            GrowthDisplayNumberFormat.TWO_DECIMALS: ".2f",
            GrowthDisplayNumberFormat.THREE_DECIMALS: ".3f",
            GrowthDisplayNumberFormat.FOUR_DECIMALS: ".4f",
        }
        return format(numeric, formats[number_format])
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        return "+".join(text for item in value if (text := _format_value(item, number_format)))
    return str(value).strip()


def _change(position: str, previous: str, proposed: str) -> GrowthDisplayNameChange:
    if previous == proposed:
        action = GrowthDisplayNameAction.UNCHANGED
    elif not previous and proposed:
        action = GrowthDisplayNameAction.ADD
    elif previous and not proposed:
        action = GrowthDisplayNameAction.CLEAR
    else:
        action = GrowthDisplayNameAction.CHANGE
    return GrowthDisplayNameChange(position, previous, proposed, action)


def _decode_csv(content: bytes | str) -> str:
    if isinstance(content, str):
        return content.removeprefix("\ufeff")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Display-name CSV must use UTF-8 encoding") from error
