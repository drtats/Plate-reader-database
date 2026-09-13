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
from plate_reader.application.services.growth_tabular_export import GrowthTabularExportBundle
from plate_reader.domain.growth import GROWTH_BACKGROUND_VERSION, GROWTH_NORMALIZATION_VERSION
from plate_reader.domain.growth.cultivation import DEFAULT_CULTIVATION_PATTERN
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
    assert bundle.metadata.row_count == 192
    measurement_rows = list(
        csv.DictReader(io.StringIO(bundle.measurements.content.decode("utf-8")))
    )
    metadata_rows = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode("utf-8"))))
    assert "Vessel" in measurement_rows[0]
    assert "Vessel" in metadata_rows[0]
    assert all(row["Vessel"] == "" for row in measurement_rows)
    assert len(measurement_rows) == 768
    assert len(metadata_rows) == 192
    assert {row["Experiment Name"] for row in metadata_rows} == {
        "Experiment 0",
        "Experiment 1",
    }
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
    assert all("no cultivation ID" in warning for warning in bundle.warnings)


@pytest.mark.parametrize("generate", [False, True])
def test_selected_run_replicates_are_stable_and_read_only(
    repository: SqlPlateReaderRepository,
    generate: bool,
) -> None:
    from plate_reader.application.services.growth_tabular_export import ExportCultivationSettings

    settings = ExportCultivationSettings(team_code="PN") if generate else None
    identifiers = itertools.count()
    importer = ImportGrowthRunService(
        repository, id_factory=lambda: f"selected-{next(identifiers):05d}"
    )
    plate_ids = []
    for index, experiment_date in enumerate((date(2026, 8, 18), date(2026, 8, 19))):
        result = importer.execute(
            ImportGrowthRun(
                ACTOR,
                f"selected-{index}.csv",
                hashlib.sha256(CSV_TEXT.encode()).hexdigest(),
                GROWTH_NORMALIZATION_VERSION,
                f"Selected {index}",
                f"Selected Plate {index}",
                experiment_date,
                idempotency_key=f"selected-export-{index}",
            ),
            CSV_TEXT,
            metadata=GrowthRunMetadata(
                project="SMS",
                plate_custom_json={}
                if generate
                else {
                    "cultivation_registry": {
                        "Team_Code": "PN",
                        "CultivationExperimentCode": f"{index + 1:03d}",
                        "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
                    }
                },
            ),
            layout_changes=(
                WellLayoutChange("A1", is_blank=True, background_group="plate"),
                WellLayoutChange("A2", is_blank=True, background_group="plate"),
                WellLayoutChange(
                    "B1",
                    strain="J3",
                    medium="LB",
                    replicate=1,
                    treatment="Drug",
                    concentration=(0.1875, 0.19)[index],
                    concentration_unit=("ug/mL", "Œºg/mL")[index],
                ),
            ),
        )
        plate_ids.append(result.plate_id)
        ComputeGrowthBackgroundService(repository).execute(
            ComputeGrowthBackgroundRevision(ACTOR, result.plate_id, GROWTH_BACKGROUND_VERSION)
        )

    def persisted_state() -> tuple[tuple[tuple[object, ...], ...], ...]:
        return tuple(
            tuple(tuple(row) for row in repository.connection.execute(query))
            for query in (
                "SELECT plate_id, custom_json FROM plates ORDER BY plate_id",
                "SELECT well_id, custom_json FROM wells ORDER BY well_id",
                "SELECT * FROM well_conditions ORDER BY well_id",
                "SELECT * FROM growth_measurements ORDER BY plate_id, well_id, channel, time_index",
                "SELECT * FROM growth_backgrounds "
                "ORDER BY revision_id, background_group, channel, time_index",
                "SELECT * FROM analysis_revisions ORDER BY revision_id",
                "SELECT * FROM provenance_events ORDER BY event_id",
            )
        )

    before = persisted_state()
    service = ExportGrowthTabularDataService(repository)
    pair = service.execute(
        ExportGrowthTabularData(
            ACTOR,
            tuple(reversed(plate_ids)),
            assign_selected_replicates=True,
            cultivation_settings=settings,
            concentration_significant_figures=2,
        )
    )
    forward = service.execute(
        ExportGrowthTabularData(
            ACTOR,
            tuple(plate_ids),
            assign_selected_replicates=True,
            cultivation_settings=settings,
            concentration_significant_figures=2,
        )
    )
    late_only = service.execute(
        ExportGrowthTabularData(
            ACTOR,
            (plate_ids[1],),
            assign_selected_replicates=True,
            cultivation_settings=settings,
            concentration_significant_figures=2,
        )
    )
    assert persisted_state() == before

    def b1_rows(bundle: GrowthTabularExportBundle) -> tuple[dict[str, str], dict[str, str]]:
        data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
        metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
        return (
            next(row for row in data if row["Well"] == "B1"),
            next(row for row in metadata if row["Well"] == "B1"),
        )

    expected = {
        str(plate_ids[0]): "PN-EXP-J3-001-B01-R1",
        str(plate_ids[1]): "PN-EXP-J3-002-B01-R2",
    }
    for bundle in (pair, forward):
        metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
        assert {
            row["Run ID"]: row["Cultivation"] for row in metadata if row["Well"] == "B1"
        } == expected
        data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
        for i, plate_id in enumerate(plate_ids, 1):
            samples = [
                row for row in data if row["Run ID"] == str(plate_id) and row["Well"] == "B1"
            ]
            assert {row["Replicate"] for row in samples} == {str(i)}
            assert {row["Local replicate"] for row in samples} == {"1"}
            assert {row["Concentration unit"] for row in samples} == {"ug/mL"}
            assert {row["Concentration"] for row in samples} == {("0.1875", "0.19")[i - 1]}
            assert {row["Matching concentration"] for row in samples} == {"0.19"}
            assert {row["Concentration matching significant figures"] for row in samples} == {"2"}
        assert bundle.measurements.row_count == 768
        assert bundle.metadata.row_count == 192
        assert all(row["Saved cultivation ID"] == "" for row in bundle.replicate_preview)
    late_data, late_meta = b1_rows(late_only)
    assert late_meta["Cultivation"] == "PN-EXP-J3-002-B01-R1"
    assert late_meta["CultivationReplicate"] == "1"
    assert late_meta["LocalReplicate"] == "1"
    assert late_data["Cultivation ID"] == late_meta["Cultivation"]
    assert late_data["Replicate"] == "1"
    assert late_data["Raw OD"] and late_data["Background Subtracted OD"]
    assert json.loads(late_meta["Well Metadata JSON"]).get("Cultivation") is None


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
