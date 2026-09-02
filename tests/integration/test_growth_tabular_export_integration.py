from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    ComputeGrowthBackgroundRevision,
    GrowthRunMetadata,
    ImportGrowthRun,
    Role,
    UserId,
    WellLayoutChange,
)
from plate_reader.application.services import (
    ComputeGrowthBackgroundService,
    ExportGrowthTabularData,
    ExportGrowthTabularDataService,
    ImportGrowthRunService,
    SaveLayoutColumnService,
)
from plate_reader.domain.growth import GROWTH_BACKGROUND_VERSION, GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
CSV_TEXT = (ROOT / "tests/fixtures/growth/with_time.csv").read_text(encoding="utf-8")
ACTOR = Actor(UserId("tabular-editor"), "tabular@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"tabular-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    try:
        yield SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def test_multi_run_export_reconciles_rows_and_does_not_write(
    repository: SqlPlateReaderRepository,
) -> None:
    identifiers = itertools.count()
    importer = ImportGrowthRunService(
        repository, id_factory=lambda: f"tabular-{next(identifiers):05d}"
    )
    plate_ids = []
    for index in range(2):
        result = importer.execute(
            ImportGrowthRun(
                ACTOR,
                f"run-{index}.csv",
                hashlib.sha256(CSV_TEXT.encode()).hexdigest(),
                GROWTH_NORMALIZATION_VERSION,
                f"Experiment {index}",
                f"Plate {index}",
                date(2026, 8, 18),
                idempotency_key=f"tabular-export-{index}",
            ),
            CSV_TEXT,
            metadata=GrowthRunMetadata(
                project="SMS",
                operator_name="Researcher",
                instrument="Synergy H1",
                experiment_custom_json={
                    "source_metadata_json": {
                        "Date": "8/18/2026",
                        "Time": "9:30:00 AM",
                        "Plate Number": f"Plate {index}",
                    }
                },
                plate_custom_json={
                    "editable_metadata_json": {
                        "Culture_Age_hours": 0.0,
                        "Culture_volume_uL": 200,
                    }
                },
            ),
            layout_changes=(
                WellLayoutChange("A1", is_blank=True, background_group="plate"),
                WellLayoutChange("A2", is_blank=True, background_group="plate"),
                WellLayoutChange(
                    "B1",
                    treatment="Mecillinam",
                    concentration=3.0,
                    concentration_unit="ug/mL",
                    replicate=1,
                ),
            ),
        )
        plate_ids.append(result.plate_id)
        ComputeGrowthBackgroundService(repository).execute(
            ComputeGrowthBackgroundRevision(ACTOR, result.plate_id, GROWTH_BACKGROUND_VERSION)
        )

    SaveLayoutColumnService(repository).execute(ACTOR, AssayType.GROWTH, "Vessel")
    counts_before = _table_counts(repository)
    bundle = ExportGrowthTabularDataService(repository).execute(
        ExportGrowthTabularData(ACTOR, tuple(plate_ids))
    )
    counts_after = _table_counts(repository)

    assert counts_after == counts_before
    assert bundle.measurements.row_count == 768
    assert bundle.metadata.row_count == 194
    measurement_rows = list(
        csv.DictReader(io.StringIO(bundle.measurements.content.decode("utf-8")))
    )
    metadata_rows = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode("utf-8"))))
    assert "Vessel" in measurement_rows[0]
    assert "Vessel" in metadata_rows[0]
    assert all(row["Vessel"] == "" for row in measurement_rows)
    assert len(measurement_rows) == 768
    assert len(metadata_rows) == 194
    assert {row["Experiment Name"] for row in measurement_rows} == {
        "Experiment 0",
        "Experiment 1",
    }
    assert all(row["Raw OD"] for row in measurement_rows)
    assert all(row["Background Mean OD"] for row in measurement_rows)
    assert all(row["Background Subtracted OD"] for row in measurement_rows)
    assert measurement_rows[0]["Date Time"] == "2026-08-18T09:30:00"
    b1 = next(row for row in measurement_rows if row["Well"] == "B1")
    assert b1["Condition 1 State"] == "Mecillinam 3.0 ug/mL"
    assert json.loads(metadata_rows[0]["Source Metadata JSON"])["Plate Number"] == "Plate 0"
    assert not bundle.warnings


def _table_counts(repository: SqlPlateReaderRepository) -> tuple[int, ...]:
    return tuple(
        int(repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in (
            "experiments",
            "plates",
            "wells",
            "growth_series_chunks",
            "growth_measurements",
            "analysis_revisions",
            "growth_backgrounds",
            "provenance_events",
        )
    )
