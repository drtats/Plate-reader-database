"""Generate deterministic, anonymous legacy-characterization fixtures."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TIMES = (20.0, 0.0, 10.0, 30.0)  # deliberately unsorted


def well_positions() -> tuple[str, ...]:
    return tuple(f"{row}{column}" for row in "ABCDEFGH" for column in range(1, 13))


def growth_value(position: str, time_minutes: float) -> float:
    if position == "A1":
        return round(0.050 + (time_minutes * 0.0001), 6)
    if position == "A2":
        return round(0.051 + (time_minutes * 0.0001), 6)
    if position == "B1":
        return round(0.001 + (time_minutes * 0.0001), 6)
    if position == "B2":
        return round(0.099 + (time_minutes * 0.0001), 6)
    index = well_positions().index(position)
    return round(0.06 + ((index % 12) * 0.002) + (time_minutes * 0.004), 6)


def write_growth_csvs() -> None:
    destination = FIXTURES / "growth"
    wells = well_positions()
    with (destination / "with_time.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Time", *wells))
        for time_minutes in TIMES:
            writer.writerow((time_minutes, *(growth_value(well, time_minutes) for well in wells)))

    with (destination / "without_time.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(wells)
        for time_minutes in (0.0, 10.0, 20.0, 30.0):
            writer.writerow(growth_value(well, time_minutes) for well in wells)

    with (destination / "labels.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in "ABCDEFGH":
            writer.writerow(f"sample_{row}{column}" for column in range(1, 13))

    with (destination / "well_metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = (
            "well",
            "is_blank",
            "bg_group",
            "display_name",
            "media",
            "strain",
            "replicate",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for position in wells:
            group = "missing" if position == "C1" else "plate"
            is_blank = position in {"A1", "A2", "B1", "B2"}
            if position in {"A1", "A2"}:
                group = "valid"
            elif position in {"B1", "B2"}:
                group = "high_cv"
            writer.writerow(
                {
                    "well": position,
                    "is_blank": is_blank,
                    "bg_group": group,
                    "display_name": f"sample_{position}",
                    "media": "Synthetic medium",
                    "strain": "Synthetic strain",
                    "replicate": 1,
                }
            )


def write_mic_csv() -> None:
    destination = FIXTURES / "mic" / "plate_cases.csv"
    concentrations = (0.5, 1.0, 2.0, 4.0)
    case_rows = {
        "A": ("normal", (0.25, 0.20, 0.10, 0.08)),
        "B": ("all_growth", (0.25, 0.25, 0.25, 0.25)),
        "C": ("all_no_growth", (0.08, 0.08, 0.08, 0.08)),
        "D": ("bounce", (0.25, 0.08, 0.25, 0.08)),
    }
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
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for position in well_positions():
            row = position[0]
            column = int(position[1:])
            if row in case_rows and column <= 4:
                case_name, values = case_rows[row]
                writer.writerow(
                    {
                        "well_position": position,
                        "od_raw": values[column - 1],
                        "is_blank": False,
                        "strain": f"strain_{case_name}",
                        "antibiotic": "compound_x",
                        "concentration": concentrations[column - 1],
                        "concentration_unit": "ug/mL",
                        "media": "Synthetic medium",
                        "replicate": 1,
                    }
                )
            else:
                writer.writerow(
                    {
                        "well_position": position,
                        "od_raw": 0.05,
                        "is_blank": True,
                        "strain": "",
                        "antibiotic": "",
                        "concentration": "",
                        "concentration_unit": "ug/mL",
                        "media": "Synthetic medium",
                        "replicate": 1,
                    }
                )


def write_legacy_growth_database() -> None:
    path = FIXTURES / "legacy" / "growth_v4.sqlite"
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE plate_meta (
            run_id TEXT PRIMARY KEY, experiment_name TEXT, user_name TEXT,
            experiment_date TEXT, od_csv_sha256 TEXT, label_csv_sha256 TEXT,
            source_fingerprint TEXT, meta_hash TEXT, app_version TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE well_meta (
            run_id TEXT, well TEXT, display_name TEXT, media TEXT, strain TEXT,
            inoculum_size TEXT, treatments TEXT, is_blank BOOLEAN, bg_group TEXT,
            custom_json TEXT, PRIMARY KEY (run_id, well)
        );
        CREATE TABLE measurements (
            run_id TEXT, well TEXT, time_min REAL, signal_type TEXT, value_raw REAL
        );
        CREATE TABLE backgrounds (
            run_id TEXT, bg_group TEXT, time_min REAL, signal_type TEXT,
            bg_mean REAL, bg_std REAL, bg_cv REAL, n_blank_wells INTEGER
        );
        CREATE TABLE provenance (
            run_id TEXT, action_type TEXT, source_filename TEXT, details_json TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    run_id = "synthetic-growth-v4"
    connection.execute(
        "INSERT INTO plate_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            "Synthetic growth fixture",
            "fixture-user",
            "2026-01-02",
            "synthetic-od-hash",
            "synthetic-label-hash",
            "/synthetic/source",
            "synthetic-meta-hash",
            "v4",
            "2026-01-02T12:00:00+00:00",
        ),
    )
    metadata_rows: list[tuple[object, ...]] = []
    measurement_rows: list[tuple[object, ...]] = []
    for position in well_positions():
        is_blank = position in {"A1", "A2", "B1", "B2"}
        group = "missing" if position == "C1" else "plate"
        if position in {"A1", "A2"}:
            group = "valid"
        elif position in {"B1", "B2"}:
            group = "high_cv"
        custom = {
            "row": position[0],
            "col": int(position[1:]),
            "raw_label": f"sample_{position}",
            "plot": position in {"C1", "C2"},
            "replicate": 1,
            "notes": "synthetic",
        }
        metadata_rows.append(
            (
                run_id,
                position,
                f"sample_{position}",
                "Synthetic medium",
                "Synthetic strain",
                "0.01",
                "compound_x",
                is_blank,
                group,
                json.dumps(custom, sort_keys=True),
            )
        )
        measurement_rows.extend(
            (run_id, position, time, "od600", growth_value(position, time)) for time in TIMES
        )
    connection.executemany(
        "INSERT INTO well_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", metadata_rows
    )
    connection.executemany("INSERT INTO measurements VALUES (?, ?, ?, ?, ?)", measurement_rows)
    for group, positions in (("valid", ("A1", "A2")), ("high_cv", ("B1", "B2"))):
        for time in TIMES:
            values = [growth_value(position, time) for position in positions]
            mean = sum(values) / len(values)
            sample_std = abs(values[1] - values[0]) / (2**0.5)
            connection.execute(
                "INSERT INTO backgrounds VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, group, time, "od600", mean, sample_std, sample_std / abs(mean), 2),
            )
    connection.execute(
        "INSERT INTO provenance VALUES (?, ?, ?, ?, ?)",
        (run_id, "create", "with_time.csv", '{"synthetic": true}', "2026-01-02T12:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def write_legacy_mic_database() -> None:
    path = FIXTURES / "legacy" / "mic_legacy.sqlite"
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE experiments (
            experiment_id TEXT PRIMARY KEY, date TEXT, person TEXT, reader TEXT,
            incubation_time REAL, inoculum_od REAL, growth_phase TEXT,
            harvest_od REAL, doubling_time REAL, notes TEXT, extra_metadata_json TEXT
        );
        CREATE TABLE plates (
            plate_id TEXT PRIMARY KEY, experiment_id TEXT, plate_name TEXT,
            plate_format INTEGER, threshold REAL, threshold_method TEXT,
            background_method TEXT, created_at TEXT, is_deleted INTEGER DEFAULT 0,
            is_locked INTEGER DEFAULT 0, is_checked INTEGER DEFAULT 0
        );
        CREATE TABLE wells (
            well_id TEXT PRIMARY KEY, plate_id TEXT, well_position TEXT, row INTEGER,
            column INTEGER, od_raw REAL, od_bg_subtracted REAL, is_blank BOOLEAN,
            strain TEXT, antibiotic TEXT, concentration REAL, concentration_unit TEXT,
            media TEXT, replicate INTEGER, growth_call BOOLEAN, notes TEXT,
            extra_labels_json TEXT, UNIQUE(plate_id, well_position)
        );
        CREATE TABLE mic_results (
            mic_result_id TEXT PRIMARY KEY, plate_id TEXT, group_id TEXT, strain TEXT,
            antibiotic TEXT, media TEXT, replicate INTEGER, mic_value REAL,
            mic_operator TEXT, mic_unit TEXT, threshold_used REAL,
            lowest_tested_conc REAL, highest_tested_conc REAL,
            concentration_values_json TEXT, num_points INTEGER,
            calculation_status TEXT, warning TEXT
        );
        CREATE TABLE saved_options (
            option_id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, value TEXT
        );
        CREATE TABLE plate_templates (
            template_id TEXT PRIMARY KEY, template_name TEXT, layout_json TEXT,
            created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "synthetic-mic-experiment",
            "2026-01-03",
            "fixture-user",
            "Synthetic reader",
            18.0,
            0.01,
            "Exponential",
            0.5,
            30.0,
            "synthetic",
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO plates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "synthetic-mic-plate",
            "synthetic-mic-experiment",
            "Synthetic MIC fixture",
            96,
            0.1,
            "fixed",
            "average_blanks",
            "2026-01-03T12:00:00+00:00",
            0,
            0,
            1,
        ),
    )
    with (FIXTURES / "mic" / "plate_cases.csv").open(encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            raw = float(row["od_raw"])
            is_blank = row["is_blank"] == "True"
            adjusted = max(0.0, raw - 0.05)
            concentration = float(row["concentration"]) if row["concentration"] else None
            growth_call = adjusted >= 0.1
            connection.execute(
                "INSERT INTO wells VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"synthetic-well-{index + 1:02d}",
                    "synthetic-mic-plate",
                    row["well_position"],
                    ord(row["well_position"][0]) - ord("A"),
                    int(row["well_position"][1:]) - 1,
                    raw,
                    adjusted,
                    is_blank,
                    row["strain"] or None,
                    row["antibiotic"] or None,
                    concentration,
                    row["concentration_unit"],
                    row["media"],
                    int(row["replicate"]),
                    growth_call,
                    "synthetic",
                    "{}",
                ),
            )
    expected = (
        ("normal", 2.0, "=", None),
        ("all_growth", 4.0, ">", None),
        ("all_no_growth", 0.5, "<=", None),
        ("bounce", 1.0, "=", "Growth bounce detected at 2.0 after no-growth at 1.0"),
    )
    for case_name, value, operator, warning in expected:
        connection.execute(
            "INSERT INTO mic_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"synthetic-result-{case_name}",
                "synthetic-mic-plate",
                f"strain_{case_name}_compound_x_Synthetic medium_1",
                f"strain_{case_name}",
                "compound_x",
                "Synthetic medium",
                1,
                value,
                operator,
                "ug/mL",
                0.1,
                0.5,
                4.0,
                "[0.5, 1.0, 2.0, 4.0]",
                4,
                "success",
                warning,
            ),
        )
    connection.commit()
    connection.close()


def main() -> None:
    write_growth_csvs()
    write_mic_csv()
    write_legacy_growth_database()
    write_legacy_mic_database()


if __name__ == "__main__":
    main()
