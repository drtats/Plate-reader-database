"""In-memory CSV normalization with no filesystem or UI coupling."""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode
from plate_reader.domain.common.plate import PLATE_96, PlateGeometry, WellPosition
from plate_reader.domain.growth.models import (
    GrowthMeasurement,
    GrowthNormalizationResult,
    NormalizationSettings,
    WellLabel,
)

_WELL_LIKE = re.compile(r"^[A-Za-z]+[0-9]+$")


@dataclass(frozen=True, slots=True)
class _ParsedRow:
    elapsed_microseconds: int
    values: tuple[float, ...]


def parse_growth_csv(
    csv_text: str,
    settings: NormalizationSettings | None = None,
    geometry: PlateGeometry = PLATE_96,
) -> GrowthNormalizationResult:
    selected_settings = settings or NormalizationSettings()
    rows = _read_rows(csv_text)
    header = tuple(value.strip() for value in rows[0])
    if not any(header):
        raise _error(IssueCode.EMPTY_INPUT, "Growth CSV header is empty.")
    data_rows = rows[1:]
    if not data_rows:
        raise _error(IssueCode.EMPTY_INPUT, "Growth CSV contains no measurement rows.")
    for row_number, row in enumerate(data_rows, start=2):
        if len(row) != len(header):
            raise _error(
                IssueCode.INVALID_VALUE,
                "Growth CSV rows must match the header width.",
                row=row_number,
                expected=len(header),
                actual=len(row),
            )

    time_columns = [index for index, value in enumerate(header) if value.casefold() == "time"]
    if len(time_columns) > 1:
        raise _error(IssueCode.DUPLICATE_TIME, "Growth CSV contains multiple Time columns.")
    time_column = time_columns[0] if time_columns else None

    issues: list[DomainIssue] = []
    well_columns: list[tuple[int, WellPosition]] = []
    seen_positions: set[WellPosition] = set()
    for index, value in enumerate(header):
        if index == time_column or not value:
            continue
        try:
            position = WellPosition.parse(value, geometry)
        except DomainValidationError:
            if _WELL_LIKE.fullmatch(value):
                raise _error(
                    IssueCode.INVALID_WELL,
                    "A well-like column is outside the plate geometry.",
                    column=value,
                ) from None
            issues.append(
                DomainIssue.warning(
                    IssueCode.UNKNOWN_COLUMN,
                    "Unrecognized non-well column was ignored.",
                    column=value,
                )
            )
            continue
        if position in seen_positions:
            raise _error(
                IssueCode.DUPLICATE_WELL,
                "Growth CSV contains the same well more than once.",
                well=position.label,
            )
        seen_positions.add(position)
        well_columns.append((index, position))
    if not well_columns:
        raise _error(IssueCode.INVALID_WELL, "Growth CSV contains no valid well columns.")
    well_columns.sort(key=lambda item: (item[1].row_index, item[1].column_index))

    expected_positions = set(geometry.positions())
    missing = tuple(
        position.label for position in geometry.positions() if position not in seen_positions
    )
    if set(seen_positions) != expected_positions:
        issues.append(
            DomainIssue.warning(
                IssueCode.MISSING_WELLS,
                "Growth CSV contains a partial plate.",
                missing_count=len(missing),
                first_missing=missing[0] if missing else "",
            )
        )

    parsed_rows: list[_ParsedRow] = []
    for input_index, row in enumerate(data_rows):
        elapsed = _elapsed_microseconds(row, input_index, time_column, selected_settings)
        values = tuple(
            _finite_float(row[column], row=input_index + 2, column=position.label)
            for column, position in well_columns
        )
        parsed_rows.append(_ParsedRow(elapsed, values))
    parsed_rows.sort(key=lambda row: row.elapsed_microseconds)
    elapsed_values = tuple(row.elapsed_microseconds for row in parsed_rows)
    if len(elapsed_values) != len(set(elapsed_values)):
        raise _error(
            IssueCode.DUPLICATE_TIME,
            "Growth CSV timepoints are duplicated at microsecond precision.",
        )

    positions = tuple(position for _, position in well_columns)
    measurements = tuple(
        GrowthMeasurement(
            position=position,
            time_index=time_index,
            elapsed_microseconds=parsed_row.elapsed_microseconds,
            channel=selected_settings.channel.strip(),
            value_raw=parsed_row.values[position_index],
        )
        for time_index, parsed_row in enumerate(parsed_rows)
        for position_index, position in enumerate(positions)
    )
    return GrowthNormalizationResult(
        measurements=measurements,
        positions=positions,
        timepoints_microseconds=elapsed_values,
        issues=tuple(issues),
    )


def parse_label_layout(csv_text: str, geometry: PlateGeometry = PLATE_96) -> tuple[WellLabel, ...]:
    rows = _read_rows(csv_text)
    if len(rows) != geometry.rows or any(len(row) != geometry.columns for row in rows):
        raise _error(
            IssueCode.INVALID_LAYOUT,
            "Label layout dimensions do not match the plate geometry.",
            expected=f"{geometry.rows}x{geometry.columns}",
            actual_rows=len(rows),
        )
    return tuple(
        WellLabel(WellPosition(row_index, column_index, geometry), rows[row_index][column_index])
        for row_index in range(geometry.rows)
        for column_index in range(geometry.columns)
    )


def _read_rows(csv_text: str) -> list[list[str]]:
    if not csv_text.strip():
        raise _error(IssueCode.EMPTY_INPUT, "CSV input is empty.")
    try:
        rows = [
            row
            for row in csv.reader(io.StringIO(csv_text), strict=True)
            if any(cell.strip() for cell in row)
        ]
    except csv.Error as error:
        raise _error(IssueCode.INVALID_VALUE, "CSV input could not be parsed.") from error
    if not rows:
        raise _error(IssueCode.EMPTY_INPUT, "CSV input is empty.")
    return rows


def _elapsed_microseconds(
    row: list[str],
    input_index: int,
    time_column: int | None,
    settings: NormalizationSettings,
) -> int:
    minutes = (
        settings.t0_offset_minutes + (input_index * settings.interval_minutes)
        if time_column is None
        else _finite_time(row[time_column], row=input_index + 2)
    )
    if minutes < 0:
        raise _error(
            IssueCode.NEGATIVE_TIME,
            "Growth time cannot be negative.",
            row=input_index + 2,
            value=minutes,
        )
    return round(minutes * 60_000_000)


def _finite_float(value: str, **context: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise _error(IssueCode.INVALID_VALUE, "Expected a numeric value.", **context) from error
    if not math.isfinite(number):
        raise _error(IssueCode.INVALID_VALUE, "Numeric values must be finite.", **context)
    return number


def _finite_time(value: str, **context: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise _error(IssueCode.INVALID_TIME, "Expected a numeric Time value.", **context) from error
    if not math.isfinite(number):
        raise _error(IssueCode.INVALID_TIME, "Time values must be finite.", **context)
    return number


def _error(code: IssueCode, message: str, **context: object) -> DomainValidationError:
    return DomainValidationError(DomainIssue.error(code, message, **context))
