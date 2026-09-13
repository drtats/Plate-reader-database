"""Plan cross-plate cultivation replicate numbers from a metadata-only projection."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeGuard

from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.growth.cultivation_conditions import (
    condition_json_object,
    cultivation_condition_key,
)


@dataclass(frozen=True, slots=True)
class ConditionReplicate:
    position: str
    replicate: int
    condition_key: str
    scope: str
    extra_fields: tuple[str, ...]
    matching_wells: int
    matching_plates: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    row: Mapping[str, object]
    plate_id: str
    position: str
    well_identity: str
    key: str
    scope: str
    saved_replicate: int | None


def plan_condition_replicates(
    rows: Sequence[Mapping[str, object]],
    *,
    target_plate_id: str,
    registry: Mapping[str, object],
) -> Mapping[str, ConditionReplicate]:
    """Number all active wells in condition/date order and return the target plan.

    Existing explicit condition-mode reservations remain reserved even for
    deleted or edited wells. Local layout replicates and legacy IDs never reserve
    numbers in this planner.
    """

    target_scope = _scope(registry.get("CultivationReplicateScope"))
    extra_fields = _extra_fields(registry.get("CultivationConditionFields"))
    candidates: list[_Candidate] = []
    used: dict[tuple[str, str], dict[int, str]] = defaultdict(dict)
    active_counts: dict[str, int] = defaultdict(int)
    active_plates: dict[str, set[str]] = defaultdict(set)
    target_positions: set[str] = set()
    target_seen = False

    for row in rows:
        plate_id = _text(row.get("plate_id"))
        if not plate_id:
            raise _error("Cultivation projection is missing plate_id")
        position = _position(row.get("position"))
        well_identity = _text(row.get("well_id")) or f"{plate_id}:{position}"
        custom = condition_json_object(row.get("custom_json"))
        saved_key = custom.get("CultivationConditionKey")
        saved_number = custom.get("CultivationReplicate")
        saved_scope = _scope(custom.get("CultivationReplicateScope"))
        if custom.get("CultivationReplicateMode") == "condition":
            if not isinstance(saved_key, str) or not saved_key or not _positive_int(saved_number):
                raise _error("Saved cultivation replicate reservation is incomplete or invalid")
            slot = (saved_scope, saved_key)
            prior = used[slot].get(saved_number)
            if prior is not None and prior != well_identity:
                raise _error(
                    "Duplicate saved cultivation replicate reservation",
                    first_well=prior,
                    second_well=well_identity,
                    replicate=saved_number,
                )
            used[slot][saved_number] = well_identity

        if plate_id == target_plate_id:
            target_seen = True
        if _deleted(row) or _blank(row):
            continue
        plate_registry = registry if plate_id == target_plate_id else _plate_registry(row)
        scope = (
            target_scope
            if plate_id == target_plate_id
            else _scope(plate_registry.get("CultivationReplicateScope"))
        )
        key = cultivation_condition_key(row, row, scope, extra_fields)
        saved_replicate = (
            saved_number
            if custom.get("CultivationReplicateMode") == "condition"
            and saved_key == key
            and saved_scope == scope
            and _positive_int(saved_number)
            else None
        )
        if plate_id == target_plate_id:
            if position in target_positions:
                raise _error("Duplicate target cultivation well position", position=position)
            target_positions.add(position)
        candidates.append(
            _Candidate(row, plate_id, position, well_identity, key, scope, saved_replicate)
        )
        active_counts[key] += 1
        active_plates[key].add(plate_id)

    if not target_seen:
        raise _error(
            "Growth plate has no cultivation replicate suggestion", plate_id=target_plate_id
        )

    planned: dict[str, ConditionReplicate] = {}
    for candidate in sorted(candidates, key=_order):
        group = (candidate.scope, candidate.key)
        if candidate.saved_replicate is None:
            replicate = 1
            while replicate in used[group]:
                replicate += 1
            used[group][replicate] = candidate.well_identity
        else:
            replicate = candidate.saved_replicate
        if candidate.plate_id == target_plate_id:
            planned[candidate.position] = ConditionReplicate(
                position=candidate.position,
                replicate=replicate,
                condition_key=candidate.key,
                scope=candidate.scope,
                extra_fields=extra_fields,
                matching_wells=active_counts[candidate.key],
                matching_plates=len(active_plates[candidate.key]),
            )
    return planned


def plan_export_condition_replicates(
    rows: Sequence[Mapping[str, object]], *, extra_fields: tuple[str, ...] = ()
) -> Mapping[tuple[str, str], ConditionReplicate]:
    """Number only selected active wells, independently of saved cultivation IDs.

    The caller supplies flattened metadata for the wells in selected export runs.
    Every selected plate's declared fields, plus the explicit export fields, form
    one comparison policy. Saved per-well fields contribute only for plates that
    have no shared field declaration. This function never mutates rows or reserves
    a number outside this one export selection.
    """

    fields = set(_extra_fields(extra_fields))
    selected: list[tuple[Mapping[str, object], str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        plate_id = _text(row.get("plate_id"))
        if not plate_id:
            raise _error("Cultivation export row is missing plate_id")
        position = _position(row.get("position"))
        if _deleted(row) or _blank(row):
            continue
        identity = (plate_id, position)
        if identity in seen:
            raise _error(
                "Duplicate selected cultivation well", plate_id=plate_id, position=position
            )
        seen.add(identity)
        registry = _plate_registry(row)
        if "CultivationConditionFields" in registry:
            fields.update(_extra_fields(registry["CultivationConditionFields"]))
        else:
            custom = condition_json_object(row.get("custom_json"))
            fields.update(_extra_fields(custom.get("CultivationConditionFields")))
        selected.append(
            (row, plate_id, position, _scope(registry.get("CultivationReplicateScope")))
        )

    effective_fields = tuple(sorted(fields))
    candidates: list[_Candidate] = []
    active_counts: dict[str, int] = defaultdict(int)
    active_plates: dict[str, set[str]] = defaultdict(set)
    for row, plate_id, position, scope in selected:
        well_identity = _text(row.get("well_id")) or f"{plate_id}:{position}"
        key = cultivation_condition_key(row, row, scope, effective_fields, normalize_units=True)
        candidates.append(_Candidate(row, plate_id, position, well_identity, key, scope, None))
        active_counts[key] += 1
        active_plates[key].add(plate_id)

    next_number: dict[str, int] = defaultdict(int)
    planned: dict[tuple[str, str], ConditionReplicate] = {}
    for candidate in sorted(candidates, key=_order):
        next_number[candidate.key] += 1
        planned[(candidate.plate_id, candidate.position)] = ConditionReplicate(
            position=candidate.position,
            replicate=next_number[candidate.key],
            condition_key=candidate.key,
            scope=candidate.scope,
            extra_fields=effective_fields,
            matching_wells=active_counts[candidate.key],
            matching_plates=len(active_plates[candidate.key]),
        )
    return planned


def _plate_registry(row: Mapping[str, object]) -> Mapping[str, object]:
    plate_custom = condition_json_object(row.get("plate_custom_json"))
    return condition_json_object(plate_custom.get("cultivation_registry"))


def _scope(value: object) -> str:
    return _text(value)


def _extra_fields(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        names: Sequence[object] = value.split(",")
    elif isinstance(value, list | tuple):
        names = value
    else:
        raise _error("CultivationConditionFields must be comma-separated field names")
    if any(not isinstance(name, str) for name in names):
        raise _error("CultivationConditionFields must contain text field names")
    return tuple(sorted({name.strip() for name in names if isinstance(name, str) and name.strip()}))


def _position(value: object) -> str:
    if not isinstance(value, str):
        raise _error("Cultivation well position must be text")
    try:
        return WellPosition.parse(value).label
    except DomainValidationError as error:
        raise _error("Invalid cultivation well position", position=value) from error


def _deleted(row: Mapping[str, object]) -> bool:
    return bool(row.get("deleted_at"))


def _blank(row: Mapping[str, object]) -> bool:
    value = row.get("is_blank")
    return value is True or value == 1 or value == "1"


def _order(candidate: _Candidate) -> tuple[int, str, int, str, str, int, int, str]:
    row = candidate.row
    raw_date = row.get("experiment_date")
    valid_date: date | None = None
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        valid_date = raw_date
    elif isinstance(raw_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
        with suppress(ValueError):
            valid_date = date.fromisoformat(raw_date)
    created_at = _text(row.get("created_at"))
    return (
        0 if valid_date is not None else 1,
        valid_date.isoformat() if valid_date is not None else "",
        0 if created_at else 1,
        created_at,
        candidate.plate_id,
        _index(row.get("row_index")),
        _index(row.get("column_index")),
        candidate.well_identity,
    )


def _index(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 10_000


def _positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _error(message: str, **context: object) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message, **context))
