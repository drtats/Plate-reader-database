"""Plate geometry and stable physical-well identity."""

from __future__ import annotations

import re
from dataclasses import dataclass

from plate_reader.domain.common.errors import DomainIssue, DomainValidationError, IssueCode

_WELL_PATTERN = re.compile(r"^(?P<row>[A-Za-z]+)0*(?P<column>[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class PlateGeometry:
    rows: int
    columns: int

    def __post_init__(self) -> None:
        if self.rows < 1 or self.rows > 26 or self.columns < 1:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_PLATE_FORMAT,
                    "Plate geometry must have 1-26 rows and at least one column.",
                    rows=self.rows,
                    columns=self.columns,
                )
            )

    @property
    def well_count(self) -> int:
        return self.rows * self.columns

    def positions(self) -> tuple[WellPosition, ...]:
        return tuple(
            WellPosition(row_index, column_index, self)
            for row_index in range(self.rows)
            for column_index in range(self.columns)
        )


@dataclass(frozen=True, slots=True, order=True)
class WellPosition:
    row_index: int
    column_index: int
    geometry: PlateGeometry

    def __post_init__(self) -> None:
        if not 0 <= self.row_index < self.geometry.rows:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_WELL,
                    "Well row is outside the plate geometry.",
                    row_index=self.row_index,
                )
            )
        if not 0 <= self.column_index < self.geometry.columns:
            raise DomainValidationError(
                DomainIssue.error(
                    IssueCode.INVALID_WELL,
                    "Well column is outside the plate geometry.",
                    column_index=self.column_index,
                )
            )

    @property
    def label(self) -> str:
        return f"{chr(ord('A') + self.row_index)}{self.column_index + 1}"

    @classmethod
    def parse(cls, value: str, geometry: PlateGeometry | None = None) -> WellPosition:
        selected_geometry = geometry or PLATE_96
        normalized = value.strip()
        match = _WELL_PATTERN.fullmatch(normalized)
        if match is None or len(match.group("row")) != 1:
            raise DomainValidationError(
                DomainIssue.error(IssueCode.INVALID_WELL, "Invalid well position.", value=value)
            )
        row_index = ord(match.group("row").upper()) - ord("A")
        column_index = int(match.group("column")) - 1
        return cls(row_index, column_index, selected_geometry)


PLATE_96 = PlateGeometry(rows=8, columns=12)
