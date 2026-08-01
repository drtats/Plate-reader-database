"""Profile realistic shared-library imports, queries, plots, export, and backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from datetime import date
from pathlib import Path

from plate_reader import __version__
from plate_reader.application.contracts import (
    Actor,
    ImportGrowthRun,
    ImportMicPlate,
    Role,
    SearchMicResults,
    UserId,
)
from plate_reader.application.demo import synthetic_growth_csv, synthetic_mic_csv
from plate_reader.application.services import (
    ImportGrowthRunService,
    ImportMicPlateService,
    LoadMicPlateService,
    PrepareGrowthPlotDataService,
    SearchMicResultsService,
)
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.domain.mic import MIC_PLATE_PARSER_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    backup_complete_database,
    connect_database,
    export_portable_runs,
)
from plate_reader.ui.plotting import (
    GrowthPlotOptions,
    endpoint_heatmap,
    growth_curve_figure,
    mic_growth_map,
    mic_plate_heatmap,
    mic_result_dot_plot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--growth-runs", type=int, default=20)
    parser.add_argument("--mic-plates", type=int, default=40)
    parser.add_argument(
        "--backend",
        choices=tuple(DatabaseBackend),
        type=DatabaseBackend,
        default=DatabaseBackend.FAKE_CLOUD,
    )
    args = parser.parse_args()
    if args.growth_runs < 1 or args.mic_plates < 1:
        parser.error("run and plate counts must be positive")
    print(
        json.dumps(
            measure(
                Path(__file__).resolve().parents[1], args.backend, args.growth_runs, args.mic_plates
            ),
            indent=2,
            sort_keys=True,
        )
    )


def measure(
    root: Path,
    backend: DatabaseBackend,
    growth_run_count: int,
    mic_plate_count: int,
) -> dict[str, object]:
    actor = Actor(UserId("workflow-benchmark"), "benchmark@example.invalid", Role.ADMIN)
    growth_source = synthetic_growth_csv()
    mic_source = synthetic_mic_csv()
    growth_import_times: list[float] = []
    mic_import_times: list[float] = []
    with tempfile.TemporaryDirectory(prefix="plate-reader-workflow-benchmark-") as directory:
        work = Path(directory)
        database = work / "library.sqlite"
        connection = connect_database(DatabaseConfig(database, backend, root / "migrations"))
        repository = SqlPlateReaderRepository(connection)
        with repository.transaction():
            repository.upsert_user(
                {
                    "user_id": actor.user_id,
                    "email": actor.email,
                    "display_name": "Workflow benchmark",
                    "role": actor.role,
                    "is_active": True,
                }
            )
        growth_ids = []
        for index in range(growth_run_count):
            source = growth_source + ("\n" * (index + 1))
            started = time.perf_counter()
            imported = ImportGrowthRunService(repository).execute(
                ImportGrowthRun(
                    actor,
                    f"growth-{index:04d}.csv",
                    hashlib.sha256(source.encode()).hexdigest(),
                    GROWTH_NORMALIZATION_VERSION,
                    f"Growth experiment {index:04d}",
                    "Growth plate 1",
                    date(2026, 1, 1),
                    idempotency_key=f"benchmark:growth:{index}",
                ),
                source,
            )
            growth_import_times.append(time.perf_counter() - started)
            growth_ids.append(imported.plate_id)
        mic_ids = []
        for index in range(mic_plate_count):
            source = mic_source + ("\n" * (index + 1))
            started = time.perf_counter()
            imported = ImportMicPlateService(repository).execute(
                ImportMicPlate(
                    actor,
                    f"mic-{index:04d}.csv",
                    hashlib.sha256(source.encode()).hexdigest(),
                    MIC_PLATE_PARSER_VERSION,
                    f"MIC experiment {index:04d}",
                    "MIC plate 1",
                    date(2026, 1, 1),
                    0.1,
                    idempotency_key=f"benchmark:mic:{index}",
                ),
                source,
            )
            mic_import_times.append(time.perf_counter() - started)
            mic_ids.append(imported.plate_id)

        timings: dict[str, float] = {}
        started = time.perf_counter()
        library_page = repository.search_runs({"limit": 25, "offset": 0})
        timings["library_page_seconds"] = elapsed(started)

        started = time.perf_counter()
        mic_results = SearchMicResultsService(repository).execute(
            SearchMicResults(actor, treatment="compound_x", limit=100)
        )
        timings["mic_result_search_seconds"] = elapsed(started)

        started = time.perf_counter()
        growth_snapshot = repository.load_plate(growth_ids[-1])
        timings["growth_plate_load_seconds"] = elapsed(started)
        if growth_snapshot is None:
            raise RuntimeError("Growth benchmark plate did not load")
        selected_positions = tuple(str(row["position"]) for row in growth_snapshot.wells[:12])
        started = time.perf_counter()
        growth_plot_data = PrepareGrowthPlotDataService().execute(
            growth_snapshot, (), selected_positions, corrected=False
        )
        growth_figure = growth_curve_figure.__wrapped__(
            growth_plot_data,
            GrowthPlotOptions(),
            "benchmark-raw",
            "benchmark-revision",
        )
        endpoint_figure = endpoint_heatmap.__wrapped__(
            growth_snapshot.raw_observations,
            growth_snapshot.wells,
            "benchmark-raw",
        )
        timings["growth_plots_seconds"] = elapsed(started)

        started = time.perf_counter()
        mic_view = LoadMicPlateService(repository).execute(actor, mic_ids[-1])
        timings["mic_plate_load_seconds"] = elapsed(started)
        started = time.perf_counter()
        mic_heatmap = mic_plate_heatmap.__wrapped__(
            mic_view.snapshot.raw_observations,
            mic_view.snapshot.wells,
            "benchmark-raw",
        )
        mic_calls = mic_growth_map.__wrapped__(
            mic_view.snapshot.wells,
            mic_view.well_calls,
            "benchmark-revision",
        )
        mic_plot = mic_result_dot_plot.__wrapped__(mic_results, "benchmark-results")
        timings["mic_plots_seconds"] = elapsed(started)

        portable = work / "selected-runs.sqlite"
        started = time.perf_counter()
        export_portable_runs(
            connection,
            portable,
            root / "migrations",
            (growth_ids[-1], mic_ids[-1]),
            exporter_version=__version__,
        )
        timings["portable_export_seconds"] = elapsed(started)

        backup = work / "complete-backup.sqlite"
        started = time.perf_counter()
        backup_complete_database(connection, backup, root / "migrations")
        timings["complete_backup_seconds"] = elapsed(started)

        counts = {
            table: int(str(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]))
            for table in ("experiments", "plates", "wells", "growth_measurements", "mic_readings")
        }
        connection.close()
        return {
            "backend": backend.value,
            "growth_runs": growth_run_count,
            "mic_plates": mic_plate_count,
            "counts": counts,
            "growth_import_seconds": summary(growth_import_times),
            "mic_import_seconds": summary(mic_import_times),
            "timings": {key: round(value, 6) for key, value in timings.items()},
            "library_page_rows": len(library_page),
            "mic_result_rows": len(mic_results),
            "plot_trace_counts": {
                "growth": len(growth_figure.data),
                "growth_endpoint": len(endpoint_figure.data),
                "mic_heatmap": len(mic_heatmap.data),
                "mic_calls": len(mic_calls.data),
                "mic_results": len(mic_plot.data),
            },
            "database_bytes": database.stat().st_size,
            "bytes_per_growth_run_with_shared_overhead": round(
                database.stat().st_size / growth_run_count
            ),
            "portable_bytes": portable.stat().st_size,
            "backup_bytes": backup.stat().st_size,
        }


def elapsed(started: float) -> float:
    return time.perf_counter() - started


def summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "median": round(ordered[len(ordered) // 2], 6),
        "p95": round(ordered[p95_index], 6),
        "max": round(ordered[-1], 6),
    }


if __name__ == "__main__":
    main()
