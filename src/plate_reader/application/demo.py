"""Deterministic synthetic data for demos and persistence benchmarks."""

from __future__ import annotations

import csv
import io
import math

from plate_reader.domain.common.plate import PLATE_96


def synthetic_growth_csv(*, duration_minutes: int = 24 * 60, interval_minutes: int = 10) -> str:
    """Return one full 96-well growth run, including both endpoints."""
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")
    if interval_minutes <= 0 or duration_minutes % interval_minutes:
        raise ValueError("interval_minutes must divide duration_minutes")

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    positions = PLATE_96.positions()
    writer.writerow(("Time", *(position.label for position in positions)))
    for time_minutes in range(0, duration_minutes + 1, interval_minutes):
        writer.writerow(
            (
                time_minutes,
                *(
                    _synthetic_od(time_minutes, position.row_index, position.column_index)
                    for position in positions
                ),
            )
        )
    return output.getvalue()


def _synthetic_od(time_minutes: int, row_index: int, column_index: int) -> str:
    baseline = 0.035 + (row_index * 0.002) + (column_index * 0.001)
    carrying_capacity = 0.7 + (column_index * 0.015)
    midpoint = 360 + (row_index * 18)
    rate = 0.012 + (column_index * 0.0002)
    value = baseline + carrying_capacity / (1 + math.exp(-rate * (time_minutes - midpoint)))
    return f"{value:.6f}"


def synthetic_mic_csv() -> str:
    """Return a deterministic 96-well endpoint plate with four MIC behaviors."""
    output = io.StringIO(newline="")
    fields = (
        "well_position",
        "od_raw",
        "is_blank",
        "strain",
        "antibiotic",
        "concentration",
        "concentration_unit",
        "media",
        "replicate",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    patterns = {
        0: ("strain_normal", (0.25, 0.20, 0.10, 0.08)),
        1: ("strain_all_growth", (0.25, 0.25, 0.25, 0.25)),
        2: ("strain_all_no_growth", (0.08, 0.08, 0.08, 0.08)),
        3: ("strain_bounce", (0.25, 0.08, 0.25, 0.08)),
    }
    concentrations = (0.5, 1.0, 2.0, 4.0)
    for position in PLATE_96.positions():
        pattern = patterns.get(position.row_index)
        active = pattern is not None and position.column_index < 4
        writer.writerow(
            {
                "well_position": position.label,
                "od_raw": pattern[1][position.column_index] if active and pattern else 0.05,
                "is_blank": not active,
                "strain": pattern[0] if active and pattern else "",
                "antibiotic": "compound_x" if active else "",
                "concentration": concentrations[position.column_index] if active else "",
                "concentration_unit": "ug/mL",
                "media": "Synthetic medium",
                "replicate": 1,
            }
        )
    return output.getvalue()
