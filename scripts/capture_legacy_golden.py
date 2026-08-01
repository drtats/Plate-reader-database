"""Capture golden outputs by executing the unmodified legacy algorithms.

Only synthetic fixtures are read and only anonymized JSON is written here. The
legacy roots are supplied explicitly so this script never guesses where user data
lives.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = FIXTURES / "golden"


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        if pd.isna(value):
            return None
        return round(value, 12)
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _write_json(name: str, value: object) -> None:
    (GOLDEN / name).write_text(
        json.dumps(_clean(value), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def capture_growth(growth_root: Path) -> None:
    sys.path.insert(0, str(growth_root))
    try:
        processing = importlib.import_module("src.processing")
        with_time = pd.read_csv(FIXTURES / "growth" / "with_time.csv")
        without_time = pd.read_csv(FIXTURES / "growth" / "without_time.csv")
        metadata = pd.read_csv(FIXTURES / "growth" / "well_metadata.csv")

        normalized_with = processing.normalize_od_data(with_time, 0.0, 10.0)
        normalized_without = processing.normalize_od_data(without_time, 5.0, 10.0)
        if normalized_with is None or normalized_without is None:
            raise RuntimeError("Legacy growth normalizer rejected a synthetic fixture")
        normalized_with = normalized_with.sort_values(["time_min", "well"]).reset_index(drop=True)
        normalized_without = normalized_without.sort_values(["time_min", "well"]).reset_index(
            drop=True
        )

        with_run = normalized_with.assign(run_id="synthetic-growth-v4")
        backgrounds, error = processing.compute_backgrounds(with_run, metadata)
        if backgrounds is None:
            raise RuntimeError(f"Legacy background calculation failed: {error}")
        backgrounds = backgrounds.sort_values(["bg_group", "time_min"]).reset_index(drop=True)
        subtracted = processing.get_subtracted_data_for_plot(with_run, backgrounds, metadata)

        _write_json("growth_normalized_with_time.json", normalized_with.to_dict(orient="records"))
        _write_json(
            "growth_normalized_without_time.json", normalized_without.to_dict(orient="records")
        )
        _write_json("growth_backgrounds.json", backgrounds.to_dict(orient="records"))
        _write_json(
            "growth_background_edge_cases.json",
            {
                "groups_emitted": sorted(backgrounds["bg_group"].unique().tolist()),
                "missing_group_emitted": "missing" in backgrounds["bg_group"].values,
                "missing_group_c1": subtracted[subtracted["well"] == "C1"][
                    ["time_min", "value_raw", "bg_mean", "value_bgsub"]
                ].to_dict(orient="records"),
                "high_cv_minimum": backgrounds[backgrounds["bg_group"] == "high_cv"]["bg_cv"].min(),
            },
        )
    finally:
        sys.path.remove(str(growth_root))
        for module_name in tuple(sys.modules):
            if module_name == "src" or module_name.startswith("src."):
                del sys.modules[module_name]


def capture_mic(mic_root: Path) -> None:
    sys.path.insert(0, str(mic_root))
    try:
        models = importlib.import_module("models")
        background = importlib.import_module("background")
        mic_calc = importlib.import_module("mic_calc")
        frame = pd.read_csv(FIXTURES / "mic" / "plate_cases.csv")
        wells = []
        for index, row in frame.iterrows():
            position = str(row["well_position"])
            concentration = None if pd.isna(row["concentration"]) else float(row["concentration"])
            wells.append(
                models.WellData(
                    well_id=f"synthetic-well-{index + 1:02d}",
                    plate_id="synthetic-mic-plate",
                    well_position=position,
                    row=ord(position[0]) - ord("A"),
                    column=int(position[1:]) - 1,
                    od_raw=float(row["od_raw"]),
                    is_blank=bool(row["is_blank"]),
                    strain=None if pd.isna(row["strain"]) else str(row["strain"]),
                    antibiotic=None if pd.isna(row["antibiotic"]) else str(row["antibiotic"]),
                    concentration=concentration,
                    concentration_unit=str(row["concentration_unit"]),
                    media=str(row["media"]),
                    replicate=int(row["replicate"]),
                )
            )

        background_value = background.calculate_background(wells)
        background.subtract_background(wells, background_value)
        background.apply_threshold(wells, 0.1)
        results = mic_calc.group_and_calculate_mics(wells)
        result_records = []
        for result in results:
            record = result.model_dump(exclude={"mic_result_id", "plate_id"})
            record["concentration_values"] = json.loads(record.pop("concentration_values_json"))
            result_records.append(record)
        result_records.sort(key=lambda record: str(record["strain"]))
        well_calls = [
            {
                "well_position": well.well_position,
                "od_raw": well.od_raw,
                "od_bg_subtracted": well.od_bg_subtracted,
                "growth_call": well.growth_call,
            }
            for well in wells
            if not well.is_blank
        ]
        _write_json(
            "mic_endpoint.json",
            {
                "background_value": background_value,
                "threshold": 0.1,
                "well_calls": well_calls,
                "results": result_records,
            },
        )
    finally:
        sys.path.remove(str(mic_root))
        for module_name in ("models", "background", "mic_calc"):
            sys.modules.pop(module_name, None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--growth-root", required=True, type=Path)
    parser.add_argument("--mic-root", required=True, type=Path)
    args = parser.parse_args()
    capture_growth(args.growth_root.resolve())
    capture_mic(args.mic_root.resolve())


if __name__ == "__main__":
    main()
