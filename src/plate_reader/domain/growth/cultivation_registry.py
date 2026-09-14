"""Pure planning and validation of persistent, plate-scoped cultivation identities."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise

from plate_reader.domain.common import DomainIssue, DomainValidationError, IssueCode, WellPosition
from plate_reader.domain.growth.cultivation_conditions import (
    condition_json_object,
    cultivation_condition_key,
    normalize_cultivation_condition_fields,
    validate_concentration_decimal_places,
    validate_concentration_matching,
    validate_concentration_precision,
)

SCHEME = "plate_condition_v1"
PLATE_CONDITION_PATTERN = "{team}-EXP-{strain}-{system}{plate}{condition}R{replicate}"
_TEAM = re.compile(r"[A-Za-z0-9]+\Z")
_SYSTEM = re.compile(r"[A-Za-z](?:[A-Za-z0-9]*[A-Za-z])?\Z")
_NUMBER = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True)
class RegistrySettings:
    team_code: str = ""
    system_code: str = ""
    concentration_significant_figures: int | None = None
    condition_fields: tuple[str, ...] = ()
    concentration_decimal_places: int | None = 2
    concentration_exact: bool = False
    reassign_changed_conditions: bool = False


@dataclass(frozen=True)
class RegistryPlatePlan:
    plate_id: str
    plate_number: str
    registry: dict[str, object]
    assignments: tuple[dict[str, object], ...]
    warnings: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    reassignments: tuple[tuple[str, str, str], ...] = ()


def plan_plate_condition_cultivations(
    rows: Sequence[Mapping[str, object]],
    plate_ids: tuple[str, ...],
    *,
    settings: RegistrySettings,
) -> tuple[RegistryPlatePlan, ...]:
    """Plan the entire library before returning selected plates; never mutate input.

    A saved plate number belongs to one plate forever, including soft-deleted
    plates. Saved well identities own their condition and replicate slots even if
    a well subsequently becomes inactive. A preview cannot act as a correction.
    """

    requested_decimal, requested_significant = _requested_precision(settings)
    if not isinstance(settings.reassign_changed_conditions, bool):
        raise _error("Reassign changed conditions must be a boolean")
    requested_fields = _fields(settings.condition_fields)
    if (
        not plate_ids
        or len(set(plate_ids)) != len(plate_ids)
        or any(not _text(p) for p in plate_ids)
    ):
        raise _error("Select distinct nonempty growth plate IDs")
    selected = set(plate_ids)
    by_plate: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    positions: set[tuple[str, str]] = set()
    identities: set[str] = set()
    for row in rows:
        plate_id = _text(row.get("plate_id"))
        well_id = _text(row.get("well_id"))
        if not plate_id or not well_id:
            raise _error("Growth well projection requires plate_id and stable well_id")
        position = _position(row.get("position"))
        if (plate_id, position) in positions or well_id in identities:
            raise _error("Duplicate physical growth well or internal identity", plate_id=plate_id)
        positions.add((plate_id, position))
        identities.add(well_id)
        by_plate[plate_id].append(row)
    if not selected <= by_plate.keys():
        raise _error("Selected Growth plate is missing from the library projection")
    if any(any(_text(row.get("deleted_at")) for row in by_plate[p]) for p in selected):
        raise _error("Deleted Growth plates cannot receive new cultivation identities")

    plate_reservations: dict[int, str] = {}
    saved_numbers: dict[str, int] = {}
    saved_ids: dict[str, str] = {}
    historical_ids: dict[str, set[str]] = defaultdict(set)
    active_other_ids: dict[str, str] = {}
    group_reservations: dict[str, dict[int, str]] = defaultdict(dict)
    replicate_reservations: dict[tuple[str, int], dict[int, str]] = defaultdict(dict)
    for plate_id, plate_rows in by_plate.items():
        registry = _registry(plate_rows[0])
        registry_scheme = registry.get("scheme")
        if registry_scheme == SCHEME:
            reserved_number = _plate_number(registry.get("CultivationPlateNumber"))
            saved_numbers[plate_id] = reserved_number
        for row in plate_rows:
            custom = condition_json_object(row.get("custom_json"))
            well_id = _text(row.get("well_id"))
            old_id = _text(custom.get("Cultivation"))
            history = custom.get("PreviousCultivationIDs", [])
            if not isinstance(history, list) or any(not isinstance(item, str) for item in history):
                raise _error("PreviousCultivationIDs must be a list of IDs", well=well_id)
            for historical_id in history:
                if historical_id.strip():
                    historical_ids[historical_id.strip()].add(well_id)
            if custom.get("CultivationNumberingScheme") != SCHEME:
                if old_id:
                    active_other_ids[old_id] = well_id
                continue
            identity = _saved_identity(row, row, validate_current=plate_id in selected)
            reserved_number = _plate_number(identity["CultivationPlateNumber"])
            if plate_id in saved_numbers and saved_numbers[plate_id] != reserved_number:
                raise _error("Saved plate and well cultivation numbers disagree", plate_id=plate_id)
            saved_numbers[plate_id] = reserved_number
            group = _group_number(identity["CultivationConditionNumber"])
            key = _text(identity["CultivationConditionKey"])
            previous = group_reservations[plate_id].get(group)
            if previous is not None and previous != key:
                raise _error(
                    "Saved condition number belongs to different conditions", plate_id=plate_id
                )
            group_reservations[plate_id][group] = key
            replicate = identity["CultivationReplicate"]
            assert isinstance(replicate, int)
            owner = replicate_reservations[(plate_id, group)].get(replicate)
            if owner is not None and owner != well_id:
                raise _error("Duplicate saved technical replicate", plate_id=plate_id)
            replicate_reservations[(plate_id, group)][replicate] = well_id
            if old_id in saved_ids and saved_ids[old_id] != well_id:
                raise _error("Duplicate saved cultivation ID", cultivation=old_id)
            saved_ids[old_id] = well_id
    for plate_id, saved_number in saved_numbers.items():
        previous_owner = plate_reservations.get(saved_number)
        if previous_owner is not None and previous_owner != plate_id:
            raise _error("Cultivation plate number is reserved by two plates", number=saved_number)
        plate_reservations[saved_number] = plate_id
    next_plate = 1
    for plate_id in sorted(by_plate, key=lambda p: _chronology(by_plate[p][0], p)):
        if plate_id not in saved_numbers:
            while next_plate in plate_reservations:
                next_plate += 1
            saved_numbers[plate_id] = next_plate
            plate_reservations[next_plate] = plate_id
            next_plate += 1

    plans: dict[str, RegistryPlatePlan] = {}
    for plate_id in plate_ids:
        plate_rows = sorted(by_plate[plate_id], key=lambda r: _well_order(r.get("position")))
        old_registry = _registry(plate_rows[0])
        existing = old_registry.get("scheme") == SCHEME
        number = _number(saved_numbers[plate_id])
        saved_decimal, saved_significant = (
            _stored_precision(old_registry) if existing else (None, None)
        )
        upgrading = (
            existing
            and "CultivationConcentrationDecimalPlaces" not in old_registry
            and requested_significant is None
        )
        decimal, significant = (
            (requested_decimal, requested_significant)
            if upgrading or not existing
            else (saved_decimal, saved_significant)
        )
        fields = (
            _fields(old_registry.get("CultivationConditionFields"))
            if existing
            else requested_fields
        )
        if existing and (
            (decimal, significant) != (requested_decimal, requested_significant)
            or fields != requested_fields
        ):
            raise _error("Saved cultivation matching rules cannot be changed", plate_id=plate_id)
        precision_metadata = (
            {"CultivationConcentrationSignificantFigures": significant}
            if existing
            and not upgrading
            and "CultivationConcentrationDecimalPlaces" not in old_registry
            else _precision_metadata(decimal, significant)
        )
        scope = _text(old_registry.get("CultivationReplicateScope"))
        eligible = any(
            not _blank(row.get("is_blank")) and _text(row.get("strain")) for row in plate_rows
        )
        team = _resolved_code(settings.team_code, old_registry, plate_rows, "Team_Code", _TEAM)
        system = _resolved_code(
            settings.system_code, old_registry, plate_rows, "CultivationSystemCode", _SYSTEM
        )
        if eligible and (not team or not system):
            raise _error("Eligible cultivations require team and system codes", plate_id=plate_id)
        has_saved_well = any(
            condition_json_object(row.get("custom_json")).get("CultivationNumberingScheme")
            == SCHEME
            for row in plate_rows
        )
        if (
            existing
            and has_saved_well
            and (
                team != _text(old_registry.get("Team_Code"))
                or system != _text(old_registry.get("CultivationSystemCode"))
            )
        ):
            raise _error("Saved cultivation team/system cannot be changed", plate_id=plate_id)

        groups = group_reservations[plate_id].copy()
        retired_groups = _retired_groups(old_registry.get("RetiredCultivationConditionNumbers"))
        if retired_groups & groups.keys():
            raise _error(
                "Retired cultivation condition number is still assigned", plate_id=plate_id
            )
        reassigning = False
        if upgrading:
            target_rule = "exact" if settings.concentration_exact else "decimal-place"
            # Permit an explicit reviewed metadata migration only when each saved
            # group maps bijectively to one group under the requested rule.
            old_to_new: dict[int, str] = {}
            new_to_old: dict[str, int] = {}
            for row in plate_rows:
                custom = condition_json_object(row.get("custom_json"))
                if custom.get("CultivationNumberingScheme") != SCHEME:
                    continue
                group = _group_number(custom["CultivationConditionNumber"])
                new_key = cultivation_condition_key(
                    row,
                    row,
                    _text(old_registry.get("CultivationReplicateScope")),
                    fields,
                    normalize_units=True,
                    concentration_decimal_places=decimal,
                    concentration_significant_figures=significant,
                )
                if (group in old_to_new and old_to_new[group] != new_key) or (
                    new_key in new_to_old and new_to_old[new_key] != group
                ):
                    if not settings.reassign_changed_conditions:
                        raise _error(
                            f"{target_rule.capitalize()} matching changes saved condition groups; "
                            "select "
                            "'Reassign condition IDs if rounding changes groups', then preview "
                            "and save the reviewed IDs",
                            plate_id=plate_id,
                        )
                    reassigning = True
                old_to_new[group] = new_key
                new_to_old[new_key] = group
            if reassigning:
                # Keep an old group number with its first encountered new key.
                # Retire all other old numbers, so changed IDs cannot later be
                # assigned to an unrelated well.
                old_numbers = set(groups) | retired_groups
                groups = {}
                key_to_reserved: dict[str, int] = {}
                for row in plate_rows:
                    custom = condition_json_object(row.get("custom_json"))
                    if custom.get("CultivationNumberingScheme") != SCHEME:
                        continue
                    key = cultivation_condition_key(
                        row,
                        row,
                        _text(old_registry.get("CultivationReplicateScope")),
                        fields,
                        normalize_units=True,
                        concentration_decimal_places=decimal,
                        concentration_significant_figures=significant,
                    )
                    if key in key_to_reserved:
                        continue
                    old_group = _group_number(custom["CultivationConditionNumber"])
                    if old_group not in groups and old_group not in retired_groups:
                        group = old_group
                    else:
                        group = next(
                            (i for i in range(1, 97) if i not in old_numbers and i not in groups),
                            0,
                        )
                    if not group:
                        raise _error("A plate cannot have more than 96 cultivation conditions")
                    key_to_reserved[key] = group
                    groups[group] = key
                retired_groups = old_numbers - set(groups)
                for group in old_numbers:
                    replicate_reservations[(plate_id, group)] = {}
            else:
                groups.update(old_to_new)
        key_to_group = {key: group for group, key in groups.items()}
        if len(key_to_group) != len(groups):
            raise _error(
                "One condition is assigned multiple saved group numbers", plate_id=plate_id
            )
        assignments: list[dict[str, object]] = []
        reassignments: list[tuple[str, str, str]] = []
        warnings: list[str] = []
        missing_strain_positions: list[str] = []
        notices: list[str] = []
        if upgrading and not reassigning:
            notices.append(
                f"Saved significant-figure fingerprints will be updated to {target_rule} "
                "matching; experiment, condition, replicate, and external IDs remain unchanged."
            )
        if reassigning:
            notices.append(
                f"{target_rule.capitalize()} matching changes condition groups. "
                "Review every changed "
                "cultivation ID before saving; previous IDs will be retained in history."
            )
        strain_aliases: dict[str, str] = {}
        for row in plate_rows:
            custom = condition_json_object(row.get("custom_json"))
            pos = _position(row.get("position"))
            well_id = _text(row.get("well_id"))
            assignment: dict[str, object] = {
                "Well": pos,
                "InternalCultivationID": well_id,
                "Local_Cultivation_ID": f"EXP{number}-{pos[0]}{int(pos[1:]):02d}",
                "CultivationPlateNumber": number,
            }
            was_saved = custom.get("CultivationNumberingScheme") == SCHEME
            saved = was_saved and not reassigning
            is_blank = _blank(row.get("is_blank"))
            strain = _text(row.get("strain"))
            if is_blank or not strain:
                if was_saved:
                    raise _error("Saved cultivation became blank or lost its strain", well=well_id)
                if not is_blank:
                    missing_strain_positions.append(pos)
                old_id = _text(custom.get("Cultivation"))
                if old_id:
                    history = custom.get("PreviousCultivationIDs", [])
                    if not isinstance(history, list) or any(
                        not isinstance(value, str) for value in history
                    ):
                        raise _error("PreviousCultivationIDs must be a list of IDs")
                    assignment["Cultivation"] = ""
                    assignment["PreviousCultivationIDs"] = list(dict.fromkeys([*history, old_id]))
                assignments.append(assignment)
                continue
            original_strain = strain
            strain = _strain_component(strain, location=f"experiment {number}, well {pos}")
            if strain != original_strain:
                strain_aliases[original_strain] = strain
            key = cultivation_condition_key(
                row,
                row,
                scope,
                fields,
                normalize_units=True,
                concentration_significant_figures=significant,
                concentration_decimal_places=decimal,
            )
            if saved:
                if not upgrading and key != custom["CultivationConditionKey"]:
                    raise _error("Saved cultivation conditions changed", well=well_id)
                group = _group_number(custom["CultivationConditionNumber"])
                rep = _positive_int(custom.get("CultivationReplicate"), "technical replicate")
            else:
                group = key_to_group.get(key, 0)
                if not group:
                    reserved_old = (
                        set(group_reservations[plate_id]) if reassigning else set()
                    ) | retired_groups
                    group = next(
                        (i for i in range(1, 97) if i not in groups and i not in reserved_old), 0
                    )
                    if not group:
                        raise _error(
                            "A plate cannot have more than 96 cultivation conditions",
                            plate_id=plate_id,
                        )
                    groups[group] = key
                    key_to_group[key] = group
                occupied = replicate_reservations[(plate_id, group)]
                rep = 1
                while True:
                    candidate_id = _external(team, strain, system, f"{number}{_number(group)}", rep)
                    owner = saved_ids.get(candidate_id) or active_other_ids.get(candidate_id)
                    if (
                        rep not in occupied
                        and (not reassigning or owner is None or owner == well_id)
                        and not historical_ids.get(candidate_id, set()) - {well_id}
                    ):
                        break
                    rep += 1
                occupied[rep] = well_id
            condition = _number(group)
            run = f"{number}{condition}"
            cultivation = _external(team, strain, system, run, rep)
            conflicting = saved_ids.get(cultivation) or active_other_ids.get(cultivation)
            if conflicting is not None and conflicting != well_id:
                raise _error(
                    "Cultivation ID collides with another saved ID", cultivation=cultivation
                )
            if historical_ids.get(cultivation, set()) - {well_id}:
                raise _error(
                    "Cultivation ID collides with another well's previous ID",
                    cultivation=cultivation,
                )
            saved_ids[cultivation] = well_id
            assignment.update(
                {
                    "Cultivation": cultivation,
                    "CultivationNumberingScheme": SCHEME,
                    "CultivationPlateNumber": number,
                    "CultivationConditionNumber": condition,
                    "CultivationRun": run,
                    "CultivationExperimentCode": number,
                    "CultivationIDPattern": PLATE_CONDITION_PATTERN,
                    "CultivationReplicateMode": "plate_condition",
                    "CultivationReplicate": rep,
                    "TechnicalReplicate": rep,
                    "BiologicalReplicateGroup": run,
                    "Team_Code": team,
                    "CultivationSystemCode": system,
                    "CultivationConditionKey": key,
                    "CultivationConditionFields": list(fields),
                    "CultivationReplicateScope": scope,
                    "LocalReplicate": custom.get("LocalReplicate", row.get("replicate")),
                }
            )
            assignment.update(precision_metadata)
            if (
                not saved
                and _text(custom.get("Cultivation"))
                and custom["Cultivation"] != cultivation
            ):
                if was_saved:
                    reassignments.append((pos, str(custom["Cultivation"]), cultivation))
                history = custom.get("PreviousCultivationIDs", [])
                if not isinstance(history, list) or any(
                    not isinstance(value, str) for value in history
                ):
                    raise _error("PreviousCultivationIDs must be a list of IDs")
                assignment["PreviousCultivationIDs"] = list(
                    dict.fromkeys([*history, custom["Cultivation"]])
                )
            assignments.append(assignment)
        for original, code in strain_aliases.items():
            notices.append(
                f"Strain {original!r} uses {code!r} in cultivation IDs; "
                "original strain metadata is unchanged"
            )
        if missing_strain_positions:
            warnings.append(
                f"Missing strain in wells {', '.join(missing_strain_positions)}; "
                "add strain names in Layout, then preview and save IDs again. "
                "These wells retain internal IDs and data, but have no external cultivation ID."
            )
        _set_ranges(assignments)
        registry = dict(old_registry)
        registry.update(
            {
                "scheme": SCHEME,
                "CultivationNumberingScheme": SCHEME,
                "CultivationPlateNumber": number,
                "CultivationIDPattern": PLATE_CONDITION_PATTERN,
                "CultivationExperimentCode": number,
                "CultivationConditionFields": list(fields),
                "CultivationReplicateScope": scope,
                "CultivationExperiment": "; ".join(
                    dict.fromkeys(
                        str(a["CultivationExperiment"])
                        for a in assignments
                        if "CultivationExperiment" in a
                    )
                ),
            }
        )
        registry.update(precision_metadata)
        if retired_groups:
            registry["RetiredCultivationConditionNumbers"] = [
                _number(group) for group in sorted(retired_groups)
            ]
        if upgrading:
            registry.pop("CultivationConcentrationSignificantFigures", None)
        if team:
            registry["Team_Code"] = team
        if system:
            registry["CultivationSystemCode"] = system
        plans[plate_id] = RegistryPlatePlan(
            plate_id,
            number,
            registry,
            tuple(assignments),
            tuple(warnings),
            tuple(notices),
            tuple(reassignments),
        )
    return tuple(plans[plate_id] for plate_id in plate_ids)


def saved_plate_condition_identity(
    well: Mapping[str, object], metadata: Mapping[str, object]
) -> dict[str, object]:
    """Validate the saved ID and fingerprint against the current physical well."""

    return _saved_identity(well, metadata, validate_current=True)


def _saved_identity(
    well: Mapping[str, object], metadata: Mapping[str, object], *, validate_current: bool
) -> dict[str, object]:
    """Reservation reads validate components even when an inactive well was edited."""

    custom = condition_json_object(well.get("custom_json"))
    if custom.get("CultivationNumberingScheme") != SCHEME:
        raise _error("Well has no saved plate-condition cultivation identity")
    registry = _registry(metadata)
    number = _number(_plate_number(custom.get("CultivationPlateNumber")))
    condition = _number(_group_number(custom.get("CultivationConditionNumber")))
    rep = _positive_int(custom.get("CultivationReplicate"), "technical replicate")
    team = _validated_component(custom.get("Team_Code"), _TEAM, "team")
    system = _validated_component(custom.get("CultivationSystemCode"), _SYSTEM, "system")
    if validate_current and _blank(well.get("is_blank")):
        raise _error("Saved cultivation is now marked blank")
    run = f"{number}{condition}"
    saved_cultivation = custom.get("Cultivation")
    if not isinstance(saved_cultivation, str):
        raise _error("Saved cultivation ID is missing")
    expected = re.fullmatch(
        rf"{re.escape(team)}-EXP-(.+)-"
        rf"{re.escape(system + run)}R{rep}",
        saved_cultivation,
    )
    if expected is None:
        raise _error("Saved cultivation identity has inconsistent component", field="Cultivation")
    strain = _strain_component(
        well.get("strain") if validate_current else expected[1],
        location=f"experiment {number}, well {_text(well.get('position'))}",
    )
    required: dict[str, object] = {
        "InternalCultivationID": _text(well.get("well_id")),
        "CultivationNumberingScheme": SCHEME,
        "CultivationPlateNumber": number,
        "CultivationConditionNumber": condition,
        "CultivationRun": run,
        "CultivationExperimentCode": number,
        "CultivationIDPattern": PLATE_CONDITION_PATTERN,
        "CultivationReplicateMode": "plate_condition",
        "CultivationReplicate": rep,
        "TechnicalReplicate": rep,
        "BiologicalReplicateGroup": run,
        "Team_Code": team,
        "CultivationSystemCode": system,
        "Cultivation": _external(team, strain, system, run, rep),
    }
    if not required["InternalCultivationID"]:
        raise _error("Saved cultivation requires stable well_id")
    for field, value in required.items():
        if custom.get(field) != value:
            raise _error("Saved cultivation identity has inconsistent component", field=field)
    position = _position(well.get("position"))
    # Accept the former generated label when reading existing assignments.
    if custom.get("Local_Cultivation_ID") not in {
        f"EXP{number}-{position[0]}{int(position[1:]):02d}",
        f"P{number}-{position[0]}{int(position[1:]):02d}",
    }:
        raise _error("Saved local cultivation position is inconsistent")
    fields = _fields(custom.get("CultivationConditionFields"))
    decimal, significant = _stored_precision(custom)
    scope = _text(custom.get("CultivationReplicateScope"))
    key = custom.get("CultivationConditionKey")
    if not isinstance(key, str) or not key:
        raise _error("Saved cultivation condition fingerprint is missing")
    if validate_current and key != cultivation_condition_key(
        well,
        metadata,
        scope,
        fields,
        normalize_units=True,
        concentration_significant_figures=significant,
        concentration_decimal_places=decimal,
    ):
        raise _error("Saved cultivation condition fingerprint changed")
    if validate_current and registry.get("scheme") == SCHEME:
        shared = {
            "CultivationPlateNumber": number,
            "CultivationIDPattern": PLATE_CONDITION_PATTERN,
            "CultivationExperimentCode": number,
            "CultivationConditionFields": list(fields),
            "CultivationReplicateScope": scope,
            "Team_Code": team,
            "CultivationSystemCode": system,
        }
        for field, value in shared.items():
            if registry.get(field) != value:
                raise _error("Saved plate and well cultivation rules disagree", field=field)
        if _stored_precision(registry) != (decimal, significant):
            raise _error("Saved plate and well cultivation rules disagree", field="concentration")
    return {
        **required,
        "Local_Cultivation_ID": custom["Local_Cultivation_ID"],
        "CultivationConditionKey": key,
        "CultivationConditionFields": list(fields),
        **_precision_metadata(decimal, significant),
        "CultivationReplicateScope": scope,
        "CultivationExperiment": custom.get("CultivationExperiment", ""),
    }


def _assigned_strain(assignment: Mapping[str, object]) -> str:
    """Read between known components; strain names may themselves contain hyphens."""

    prefix = f"{assignment['Team_Code']}-EXP-"
    suffix = (
        f"-{assignment['CultivationSystemCode']}{assignment['CultivationRun']}"
        f"R{assignment['CultivationReplicate']}"
    )
    return str(assignment["Cultivation"])[len(prefix) : -len(suffix)]


def _strain_component(value: object, *, location: str) -> str:
    """Use underscores for spaces/hyphens and d for delta; retain original metadata."""

    strain = _text(value)
    if not strain or not strain.isprintable():
        raise _error(
            f"Invalid cultivation strain {strain!r} at {location}: "
            "use a nonempty name without control or nonprinting characters"
        )
    return re.sub(r"\s+", "_", strain).replace("-", "_").replace("Δ", "d").replace("δ", "d")


def _set_ranges(assignments: list[dict[str, object]]) -> None:
    runs: dict[str, set[str]] = defaultdict(set)
    for assignment in assignments:
        if assignment.get("Cultivation"):
            strain = _assigned_strain(assignment)
            runs[strain].add(str(assignment["CultivationRun"]))
    for assignment in assignments:
        if not assignment.get("Cultivation"):
            continue
        strain = _assigned_strain(assignment)
        ordered = sorted(runs[strain], key=lambda number: (int(number), number))
        contiguous = len(ordered) > 1 and all(
            int(after[-2:]) == int(before[-2:]) + 1 for before, after in pairwise(ordered)
        )
        expression = f"{ordered[0]}-{ordered[-1]}" if contiguous else ",".join(ordered)
        assignment["CultivationExperiment"] = (
            f"{assignment['Team_Code']}-EXP-{strain}-{assignment['CultivationSystemCode']}"
            f"[{expression}]"
        )


def _registry(row: Mapping[str, object]) -> dict[str, object]:
    plate = condition_json_object(row.get("plate_custom_json"))
    return condition_json_object(plate.get("cultivation_registry"))


def _fields(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple | list):
        return normalize_cultivation_condition_fields(tuple(value))
    raise _error("CultivationConditionFields must be a list of names")


def _precision(value: object) -> int | None:
    validate_concentration_precision(value)  # type: ignore[arg-type]
    return value  # type: ignore[return-value]


def _requested_precision(settings: RegistrySettings) -> tuple[int | None, int | None]:
    if not isinstance(settings.concentration_exact, bool):
        raise _error("Concentration exact mode must be a boolean")
    if settings.concentration_exact:
        if settings.concentration_significant_figures is not None:
            raise _error("Exact concentration matching cannot also specify significant figures")
        return None, None
    # The third positional argument is the historical significant-figure API.
    # Its explicit use takes precedence over the new default of two decimal places.
    if settings.concentration_significant_figures is not None:
        return None, _precision(settings.concentration_significant_figures)
    validate_concentration_decimal_places(settings.concentration_decimal_places)
    return settings.concentration_decimal_places, None


def _stored_precision(source: Mapping[str, object]) -> tuple[int | None, int | None]:
    if "CultivationConcentrationDecimalPlaces" in source:
        decimal = source["CultivationConcentrationDecimalPlaces"]
        validate_concentration_decimal_places(decimal)  # type: ignore[arg-type]
        return decimal, None  # type: ignore[return-value]
    return None, _precision(source.get("CultivationConcentrationSignificantFigures"))


def _precision_metadata(decimal: int | None, significant: int | None) -> dict[str, object]:
    validate_concentration_matching(significant, decimal)
    return (
        {"CultivationConcentrationDecimalPlaces": decimal}
        if significant is None
        else {"CultivationConcentrationSignificantFigures": significant}
    )


def _resolved_code(
    requested: str,
    registry: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    field: str,
    pattern: re.Pattern[str],
) -> str:
    values = {
        value
        for row in rows
        if (value := _text(condition_json_object(row.get("custom_json")).get(field)))
    }
    shared = _text(registry.get(field))
    value = _text(requested) or shared or (next(iter(values)) if len(values) == 1 else "")
    return _validated_component(value, pattern, field) if value else ""


def _validated_component(value: object, pattern: re.Pattern[str], field: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _error("Invalid or missing cultivation ID component", field=field)
    return value


def _external(team: str, strain: str, system: str, run: str, replicate: int) -> str:
    return f"{team}-EXP-{strain}-{system}{run}R{replicate}"


def _number(value: int) -> str:
    return str(value).zfill(2)


def _plate_number(value: object) -> int:
    if not isinstance(value, str) or _NUMBER.fullmatch(value) is None or len(value) < 2:
        raise _error("Saved cultivation plate number must be decimal with at least two digits")
    number = int(value)
    if number < 1 or value != _number(number):
        raise _error("Saved cultivation plate number is not canonical or positive")
    return number


def _group_number(value: object) -> int:
    number = _plate_number(value)
    if number > 96:
        raise _error("Saved cultivation condition must be between 01 and 96")
    return number


def _retired_groups(value: object) -> set[int]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise _error("Retired cultivation condition numbers must be a list")
    numbers = {_group_number(item) for item in value}
    if len(numbers) != len(value):
        raise _error("Retired cultivation condition numbers contain duplicates")
    return numbers


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _error("Saved cultivation number must be a positive integer", field=field)
    return value


def _position(value: object) -> str:
    if not isinstance(value, str):
        raise _error("Growth well has invalid position")
    return WellPosition.parse(value).label


def _well_order(value: object) -> tuple[int, int]:
    position = _position(value)
    return (ord(position[0]) - ord("A"), int(position[1:]))


def _blank(value: object) -> bool:
    return value in (True, 1, "1")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()


def _chronology(row: Mapping[str, object], plate_id: str) -> tuple[str, str, str]:
    raw = _text(row.get("experiment_date"))
    try:
        dated = date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        dated = "9999-12-31"
    created = _text(row.get("created_at"))
    with suppress(ValueError):
        created = datetime.fromisoformat(created.replace("Z", "+00:00")).isoformat()
    return dated, created, plate_id


def _error(message: str, **context: object) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_VALUE, message, **context))
