from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader import __version__
from plate_reader.application.contracts import (
    Actor,
    ComputeGrowthBackgroundRevision,
    ExportPortableRun,
    ImportGrowthRun,
    LifecycleStatus,
    PlateId,
    Role,
    SearchRuns,
    UpdatePlateMetadata,
    UpdateWellLayout,
    UserId,
    WellLayoutChange,
)
from plate_reader.application.services import (
    ComputeGrowthBackgroundService,
    ExportGrowthRunService,
    ImportGrowthRunService,
    LoadGrowthRunService,
    PreviewGrowthRunService,
    SearchGrowthRunsService,
    UpdateGrowthLayoutService,
    UpdateGrowthMetadataService,
)
from plate_reader.domain.growth import GROWTH_BACKGROUND_VERSION, GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlitePortableRunExporter,
    SqlPlateReaderRepository,
    connect_database,
    validate_portable_file,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
CSV_TEXT = (ROOT / "tests/fixtures/growth/with_time.csv").read_text(encoding="utf-8")
LABEL_TEXT = (ROOT / "tests/fixtures/growth/labels.csv").read_text(encoding="utf-8")
ACTOR = Actor(UserId("workflow-editor"), "workflow@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"workflow-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    try:
        yield SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def test_preview_is_read_only_and_reports_shape(repository: SqlPlateReaderRepository) -> None:
    preview = PreviewGrowthRunService().execute(CSV_TEXT, label_csv_text=LABEL_TEXT)

    assert preview.measurement_count == 384
    assert preview.well_count == 96
    assert preview.timepoint_count == 4
    assert preview.first_elapsed_minutes == 0
    assert preview.last_elapsed_minutes == 30
    assert preview.label_count == 96
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)


def test_growth_workflow_rejects_empty_stale_or_unsupported_requests(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    snapshot = repository.load_plate(plate_id)
    assert snapshot is not None
    with pytest.raises(ValueError, match="metadata field"):
        UpdateGrowthMetadataService(repository).execute(
            UpdatePlateMetadata(ACTOR, plate_id, str(snapshot.metadata["updated_at"]))
        )
    with pytest.raises(ValueError, match="layout change"):
        UpdateGrowthLayoutService(repository).execute(
            UpdateWellLayout(ACTOR, plate_id, str(snapshot.metadata["updated_at"]), ())
        )
    with pytest.raises(ValueError, match="Unsupported growth background"):
        ComputeGrowthBackgroundService(repository).execute(
            ComputeGrowthBackgroundRevision(ACTOR, plate_id, "growth-background/0")
        )
    with pytest.raises(PermissionError, match="admins"):
        SearchGrowthRunsService(repository).execute(SearchRuns(ACTOR, include_deleted=True))
    with pytest.raises(LookupError, match="not found"):
        LoadGrowthRunService(repository).cache_token(ACTOR, PlateId("missing"))


def test_metadata_layout_background_search_load_and_export(
    repository: SqlPlateReaderRepository, tmp_path: Path
) -> None:
    plate_id = import_run(repository)
    raw_before = raw_hash(repository, plate_id)
    initial_cache_token = LoadGrowthRunService(repository).cache_token(ACTOR, plate_id)
    initial = repository.load_plate(plate_id)
    assert initial is not None

    updated = UpdateGrowthMetadataService(repository).execute(
        UpdatePlateMetadata(
            actor=ACTOR,
            plate_id=plate_id,
            expected_updated_at=str(initial.metadata["updated_at"]),
            experiment_name="Updated experiment",
            plate_name="Updated plate",
            project="Growth project",
            instrument="Synthetic reader",
            notes="Saved explicitly",
            lifecycle_status=LifecycleStatus.FINAL,
        )
    )
    metadata_cache_token = LoadGrowthRunService(repository).cache_token(ACTOR, plate_id)
    assert metadata_cache_token != initial_cache_token
    layout = UpdateGrowthLayoutService(repository).execute(
        UpdateWellLayout(
            actor=ACTOR,
            plate_id=plate_id,
            expected_updated_at=str(updated.metadata["updated_at"]),
            changes=(
                WellLayoutChange(position="A1", is_blank=True, background_group="plate"),
                WellLayoutChange(position="A2", is_blank=True, background_group="plate"),
                WellLayoutChange(position="B1", strain="strain-b", replicate=2),
            ),
        )
    )
    revision = ComputeGrowthBackgroundService(
        repository, id_factory=lambda: "background-revision-1"
    ).execute(
        ComputeGrowthBackgroundRevision(
            actor=ACTOR,
            plate_id=plate_id,
            algorithm_version=GROWTH_BACKGROUND_VERSION,
        )
    )
    assert LoadGrowthRunService(repository).cache_token(ACTOR, plate_id) != metadata_cache_token

    assert revision.background_count == 4
    assert raw_hash(repository, plate_id) == raw_before
    assert str(layout.metadata["name"]) == "Updated experiment"
    assert str(layout.metadata["plate_name"]) == "Updated plate"
    assert str(layout.metadata["project"]) == "Growth project"
    assert str(layout.metadata["instrument"]) == "Synthetic reader"
    assert str(layout.metadata["notes"]) == "Saved explicitly"
    assert str(layout.metadata["lifecycle_status"]) == "final"

    runs = SearchGrowthRunsService(repository).execute(
        SearchRuns(actor=ACTOR, text="Updated", date_from=date(2026, 1, 1))
    )
    assert [run.plate_id for run in runs] == [plate_id]
    view = LoadGrowthRunService(repository).execute(ACTOR, plate_id)
    assert len(view.backgrounds) == 4
    assert [row["event_type"] for row in view.provenance] == [
        "growth_imported",
        "growth_metadata_updated",
        "growth_layout_updated",
        "growth_background_computed",
    ]

    artifact = ExportGrowthRunService(
        repository,
        SqlitePortableRunExporter(
            repository.connection, MIGRATIONS, exporter_version=f"plate-reader/{__version__}"
        ),
    ).execute(ExportPortableRun(ACTOR, (plate_id,), (revision.revision_id,)))
    portable_path = tmp_path / artifact.filename
    portable_path.write_bytes(artifact.content)
    preview = validate_portable_file(portable_path)
    assert preview.plate_ids == (plate_id,)
    with sqlite3.connect(portable_path) as connection:
        assert connection.execute("SELECT count(*) FROM growth_measurements").fetchone() == (384,)


def import_run(repository: SqlPlateReaderRepository) -> PlateId:
    result = ImportGrowthRunService(repository, id_factory=identifier_sequence()).execute(
        ImportGrowthRun(
            actor=ACTOR,
            source_name="workflow.csv",
            source_sha256=hashlib.sha256(CSV_TEXT.encode()).hexdigest(),
            parser_version=GROWTH_NORMALIZATION_VERSION,
            experiment_name="Workflow experiment",
            plate_name="Workflow plate",
            experiment_date=date(2026, 1, 2),
        ),
        CSV_TEXT,
        label_csv_text=LABEL_TEXT,
    )
    return result.plate_id


def identifier_sequence() -> object:
    counter = iter(range(10_000))
    return lambda: f"workflow-{next(counter):04d}"


def raw_hash(repository: SqlPlateReaderRepository, plate_id: PlateId) -> str:
    rows = repository.connection.execute(
        "SELECT well_id, channel, time_index, elapsed_microseconds, value_raw "
        "FROM growth_measurements WHERE plate_id = ? ORDER BY well_id, channel, time_index",
        (plate_id,),
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()
