from __future__ import annotations

from pathlib import Path

from scripts.benchmark_workflows import measure

from plate_reader.infrastructure.database import DatabaseBackend

ROOT = Path(__file__).resolve().parents[2]


def test_shared_library_stays_within_operational_budgets() -> None:
    report = measure(ROOT, DatabaseBackend.FAKE_CLOUD, 5, 10)
    timings = report["timings"]
    assert isinstance(timings, dict)
    assert report["counts"] == {
        "experiments": 15,
        "plates": 15,
        "wells": 1_440,
        "growth_measurements": 69_600,
        "mic_readings": 960,
    }
    assert report["library_page_rows"] == 15
    assert report["mic_result_rows"] == 40
    assert float(timings["library_page_seconds"]) < 0.25
    assert float(timings["mic_result_search_seconds"]) < 0.25
    assert float(timings["growth_plate_load_seconds"]) < 0.5
    assert float(timings["growth_plots_seconds"]) < 1.0
    assert float(timings["mic_plots_seconds"]) < 1.0
    assert float(timings["portable_export_seconds"]) < 2.0
    assert float(timings["complete_backup_seconds"]) < 5.0
    assert int(report["bytes_per_growth_run_with_shared_overhead"]) < 7_000_000
