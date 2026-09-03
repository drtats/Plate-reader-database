"""Shared metadata-only table formatting for Growth run discovery surfaces."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from plate_reader.application.ports.repositories import (
    ConcentrationRange,
    InoculumRange,
    RunSummary,
)


def run_summary_table(
    results: Sequence[RunSummary], custom_columns: Sequence[str] = ()
) -> pd.DataFrame:
    """Build a table with stable plate identifiers and Library-equivalent metadata."""

    rows = run_summary_rows(results, custom_columns)
    return pd.DataFrame.from_records(rows).set_index("plate_id")


def run_summary_rows(
    results: Sequence[RunSummary], custom_columns: Sequence[str] = ()
) -> list[dict[str, str | bool]]:
    """Format metadata-only run summaries for selectable discovery tables."""

    rows: list[dict[str, str | bool]] = []
    for run in results:
        row: dict[str, str | bool] = {
            "plate_id": str(run.plate_id),
            "Select": False,
            "Experiment": str(run.experiment_name),
            "Plate": str(run.plate_name),
            "Experiment date": str(run.experiment_date),
            "Project": _display_value(run.project),
            "Strains": _display_values(run.strains),
            "Media": _display_values(run.media),
            "Treatments": _display_values(run.treatments),
            "Concentration range": _display_numeric_ranges(run.concentration_ranges),
            "Inoculum size": _display_numeric_ranges(run.inoculum_ranges),
        }
        custom_by_name = {
            name.casefold(): values for name, values in getattr(run, "custom_fields", ())
        }
        row.update(
            {
                name: _display_values(custom_by_name.get(name.casefold(), ()))
                for name in custom_columns
            }
        )
        row["Last updated"] = str(run.updated_at)
        rows.append(row)
    return rows


def _display_value(value: object) -> str:
    """Render absent summary metadata consistently without changing stored values."""

    text = str(value).strip() if value is not None else ""
    return text or "—"


def _display_values(values: object) -> str:
    if not isinstance(values, tuple):
        return "—"
    displayed = tuple(_display_value(value) for value in values)
    return ", ".join(value for value in displayed if value != "—") or "—"


def _display_numeric_ranges(
    ranges: tuple[ConcentrationRange, ...] | tuple[InoculumRange, ...],
) -> str:
    """Format bounded numeric metadata with explicit unitless values."""

    formatted: list[str] = []
    for numeric_range in ranges:
        lower = f"{numeric_range.minimum:g}"
        upper = f"{numeric_range.maximum:g}"
        unit = _display_value(numeric_range.unit)
        value = lower if lower == upper else f"{lower}\N{EN DASH}{upper}"
        formatted.append(f"{value} (unit not set)" if unit == "—" else f"{value} {unit}")
    return ", ".join(formatted) or "—"
