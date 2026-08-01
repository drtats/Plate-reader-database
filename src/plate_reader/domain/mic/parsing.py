"""Strict long-format MIC plate CSV parsing with custom-label preservation."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Mapping

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import PLATE_96, WellPosition
from plate_reader.domain.mic.models import MicWell

MIC_PLATE_PARSER_VERSION = "mic-long-csv/1.0.0"

_ALIASES = {
    "well": "well_position",
    "position": "well_position",
    "od": "od_raw",
    "value_raw": "od_raw",
    "blank": "is_blank",
    "antibiotic": "treatment",
    "drug": "treatment",
    "media": "medium",
}
_KNOWN_COLUMNS = {
    "well_position",
    "od_raw",
    "is_blank",
    "strain",
    "treatment",
    "concentration",
    "concentration_unit",
    "medium",
    "replicate",
    "notes",
}


def parse_mic_plate_csv(text: str) -> tuple[MicWell, ...]:
    if not text.strip():
        raise DomainValidationError(
            DomainIssue.error(IssueCode.EMPTY_INPUT, "MIC plate CSV is empty.")
        )
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise _invalid("MIC plate CSV requires a header row.")
    normalized_headers = tuple(_normalize_header(header) for header in reader.fieldnames)
    if len(normalized_headers) != len(set(normalized_headers)):
        raise _invalid("MIC plate CSV contains duplicate normalized headers.")
    if not {"well_position", "od_raw"} <= set(normalized_headers):
        raise _invalid("MIC plate CSV requires well_position and od_raw columns.")
    header_map = dict(zip(reader.fieldnames, normalized_headers, strict=True))
    wells: list[MicWell] = []
    for row_number, source_row in enumerate(reader, start=2):
        row = {_mapped_header(header_map, key): value for key, value in source_row.items()}
        if not any((value or "").strip() for value in row.values()):
            continue
        try:
            wells.append(_parse_row(row))
        except (DomainValidationError, ValueError) as error:
            raise _invalid(f"MIC CSV row {row_number}: {error}") from error
    if not wells:
        raise _invalid("MIC plate CSV contains no data rows.")
    positions = [well.position for well in wells]
    if len(positions) != len(set(positions)):
        raise DomainValidationError(
            DomainIssue.error(IssueCode.DUPLICATE_WELL, "MIC plate CSV repeats a well position.")
        )
    return tuple(
        sorted(wells, key=lambda well: (well.position.row_index, well.position.column_index))
    )


def _parse_row(row: Mapping[str, str | None]) -> MicWell:
    position = WellPosition.parse(_required(row, "well_position"), PLATE_96)
    custom_labels = tuple(
        sorted(
            (key, (value or "").strip())
            for key, value in row.items()
            if key not in _KNOWN_COLUMNS and (value or "").strip()
        )
    )
    return MicWell(
        position=position,
        value_raw=_finite_float(_required(row, "od_raw"), "od_raw"),
        is_blank=_boolean(row.get("is_blank"), default=False),
        strain=_optional(row.get("strain")),
        treatment=_optional(row.get("treatment")),
        concentration=_optional_float(row.get("concentration"), "concentration"),
        concentration_unit=_optional(row.get("concentration_unit")) or "ug/mL",
        medium=_optional(row.get("medium")),
        replicate=_positive_int(row.get("replicate"), default=1),
        notes=_optional(row.get("notes")),
        custom_labels=custom_labels,
    )


def _normalize_header(value: str) -> str:
    normalized = "_".join(value.strip().casefold().replace("-", " ").split())
    return _ALIASES.get(normalized, normalized)


def _mapped_header(header_map: Mapping[str, str], key: str | None) -> str:
    if key is None:
        raise _invalid("MIC plate CSV has more values than headers.")
    return header_map[key]


def _required(row: Mapping[str, str | None], key: str) -> str:
    value = _optional(row.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional(value: str | None) -> str | None:
    normalized = "" if value is None else value.strip()
    return normalized or None


def _finite_float(value: str, field: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _optional_float(value: str | None, field: str) -> float | None:
    normalized = _optional(value)
    return None if normalized is None else _finite_float(normalized, field)


def _positive_int(value: str | None, *, default: int) -> int:
    normalized = _optional(value)
    if normalized is None:
        return default
    try:
        result = int(normalized)
    except ValueError as error:
        raise ValueError("replicate must be an integer") from error
    if result < 1:
        raise ValueError("replicate must be positive")
    return result


def _boolean(value: str | None, *, default: bool) -> bool:
    normalized = _optional(value)
    if normalized is None:
        return default
    if normalized.casefold() in {"true", "1", "yes", "y"}:
        return True
    if normalized.casefold() in {"false", "0", "no", "n"}:
        return False
    raise ValueError("is_blank must be true or false")


def _invalid(message: str) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(IssueCode.INVALID_LAYOUT, message))
