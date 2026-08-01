from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    ComputeMicRevision,
    ImportMicPlate,
    LifecycleStatus,
    MicWellLayoutChange,
    Role,
    SearchMicResults,
    SearchRuns,
    SetMicLockState,
    SetMicReviewState,
    SoftDeleteMicPlate,
    UpdateMicLayout,
    UpdateMicMetadata,
    UserId,
)
from plate_reader.application.services.authorization import AuthorizationError
from plate_reader.application.services.mic_import import ImportMicPlateService
from plate_reader.application.services.mic_workflow import (
    ComputeMicRevisionService,
    LoadMicPlateService,
    LoadMicResultSearchCatalogService,
    MicResultSearchQuery,
    RestoreMicPlateService,
    SearchMicPlatesService,
    SearchMicResultsService,
    SetMicLockStateService,
    SetMicReviewStateService,
    SoftDeleteMicPlateService,
    UpdateMicLayoutService,
    UpdateMicMetadataService,
)
from plate_reader.domain.mic import MIC_ENDPOINT_VERSION, MIC_PLATE_PARSER_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.database.repository import ConcurrencyConflictError

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MIC_CSV = (ROOT / "tests" / "fixtures" / "mic" / "plate_cases.csv").read_text(encoding="utf-8")
EDITOR = Actor(UserId("mic-editor"), "mic-editor@example.invalid", Role.EDITOR)
ADMIN = Actor(UserId("mic-admin"), "mic-admin@example.invalid", Role.ADMIN)
VIEWER = Actor(UserId("mic-viewer"), "mic-viewer@example.invalid", Role.VIEWER)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"mic-flow-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        for actor in (EDITOR, ADMIN, VIEWER):
            repository.upsert_user(
                {
                    "user_id": actor.user_id,
                    "email": actor.email,
                    "display_name": actor.email.split("@")[0],
                    "role": actor.role,
                    "is_active": True,
                }
            )
    try:
        yield repository
    finally:
        connection.close()


def test_load_and_compute_revision_preserve_raw_data(repository: SqlPlateReaderRepository) -> None:
    plate_id = seed_plate(repository)
    load = LoadMicPlateService(repository)
    first = load.execute(VIEWER, plate_id)
    before_hash = raw_hash(repository, str(plate_id))

    result = ComputeMicRevisionService(repository, id_factory=id_sequence("revision")).execute(
        ComputeMicRevision(EDITOR, plate_id, MIC_ENDPOINT_VERSION, threshold=0.15)
    )
    second = load.execute(VIEWER, plate_id)

    assert len(first.snapshot.wells) == len(first.well_calls) == 96
    assert len(first.results) == 4
    assert result.call_count == 96
    assert result.result_count == 4
    assert len(second.snapshot.revisions) == 2
    assert [row["is_current"] for row in second.snapshot.revisions] == [0, 1]
    assert second.results[0]["threshold_used"] == 0.15
    assert raw_hash(repository, str(plate_id)) == before_hash
    assert load.cache_token(VIEWER, plate_id) != ""


def test_metadata_and_layout_edits_preserve_flags_methods_and_raw(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = seed_plate(repository)
    repository.connection.execute(
        "UPDATE plates SET is_locked = 1, is_checked = 1 WHERE plate_id = ?", (plate_id,)
    )
    before = LoadMicPlateService(repository).execute(EDITOR, plate_id)
    before_hash = raw_hash(repository, str(plate_id))

    metadata_view = UpdateMicMetadataService(
        repository, id_factory=id_sequence("metadata")
    ).execute(
        UpdateMicMetadata(
            EDITOR,
            plate_id,
            str(before.snapshot.metadata["updated_at"]),
            experiment_name="Renamed MIC experiment",
            plate_name="Renamed MIC plate",
            project="MIC project",
            experiment_date=date(2026, 3, 4),
            tags=("mic", "reviewed"),
            operator_name="Researcher C",
            reader="Synergy H1",
            incubation_time_hours=20,
            inoculum_od=0.01,
            growth_phase="Exponential",
            harvest_od=0.5,
            doubling_time_minutes=32,
            instrument="Synergy H1",
            notes="Complete MIC metadata",
            threshold=0.12,
            experiment_custom_json={"batch": "M2"},
            plate_custom_json={"sealed": True},
            lifecycle_status=LifecycleStatus.FINAL,
        )
    )

    assert metadata_view.snapshot.metadata["name"] == "Renamed MIC experiment"
    assert metadata_view.snapshot.metadata["plate_name"] == "Renamed MIC plate"
    assert metadata_view.snapshot.metadata["project"] == "MIC project"
    assert metadata_view.snapshot.metadata["experiment_date"] == "2026-03-04"
    assert metadata_view.snapshot.metadata["tags"] == ("mic", "reviewed")
    assert metadata_view.snapshot.metadata["operator_name"] == "Researcher C"
    assert metadata_view.snapshot.metadata["reader"] == "Synergy H1"
    assert metadata_view.snapshot.metadata["incubation_time_hours"] == 20.0
    assert metadata_view.snapshot.metadata["inoculum_od"] == 0.01
    assert metadata_view.snapshot.metadata["growth_phase"] == "Exponential"
    assert metadata_view.snapshot.metadata["harvest_od"] == 0.5
    assert metadata_view.snapshot.metadata["doubling_time_minutes"] == 32.0
    assert metadata_view.snapshot.metadata["instrument"] == "Synergy H1"
    assert metadata_view.snapshot.metadata["notes"] == "Complete MIC metadata"
    assert metadata_view.snapshot.metadata["experiment_custom_json"] == '{"batch":"M2"}'
    assert metadata_view.snapshot.metadata["plate_custom_json"] == '{"sealed":true}'
    assert metadata_view.snapshot.metadata["plate_format"] == 96
    assert metadata_view.snapshot.metadata["threshold_method"] == "fixed"
    assert metadata_view.snapshot.metadata["background_method"] == "average_blanks"
    assert metadata_view.snapshot.metadata["is_locked"] == 1
    assert metadata_view.snapshot.metadata["is_checked"] == 1
    assert metadata_view.snapshot.metadata["deleted_at"] is None
    assert len(metadata_view.snapshot.revisions) == 2

    layout_view = UpdateMicLayoutService(repository, id_factory=id_sequence("layout")).execute(
        UpdateMicLayout(
            EDITOR,
            plate_id,
            str(metadata_view.snapshot.metadata["updated_at"]),
            (
                MicWellLayoutChange(
                    "A1",
                    display_name="edited A1",
                    strain="edited strain",
                    treatment="edited drug",
                    concentration=8,
                    concentration_unit="ug/mL",
                    medium="M9",
                    replicate=2,
                    notes="reviewed",
                    custom_labels={"oxygen": "aerobic"},
                ),
            ),
        )
    )

    a1 = next(well for well in layout_view.snapshot.wells if well["position"] == "A1")
    assert (
        a1["display_name"],
        a1["strain"],
        a1["treatment"],
        a1["concentration"],
        a1["medium"],
        a1["replicate"],
        a1["notes"],
        a1["custom_json"],
    ) == (
        "edited A1",
        "edited strain",
        "edited drug",
        8.0,
        "M9",
        2,
        "reviewed",
        '{"oxygen":"aerobic"}',
    )
    assert len(layout_view.snapshot.revisions) == 3
    assert layout_view.snapshot.metadata["is_locked"] == 1
    assert layout_view.snapshot.metadata["is_checked"] == 1
    assert raw_hash(repository, str(plate_id)) == before_hash


def test_review_lock_delete_restore_and_authorization(repository: SqlPlateReaderRepository) -> None:
    plate_id = seed_plate(repository)
    view = LoadMicPlateService(repository).execute(EDITOR, plate_id)

    checked = SetMicReviewStateService(repository).execute(
        SetMicReviewState(EDITOR, plate_id, str(view.snapshot.metadata["updated_at"]), checked=True)
    )
    assert checked.snapshot.metadata["is_checked"] == 1
    with pytest.raises(AuthorizationError):
        SetMicLockStateService(repository).execute(
            SetMicLockState(
                EDITOR,
                plate_id,
                str(checked.snapshot.metadata["updated_at"]),
                locked=True,
            )
        )
    locked = SetMicLockStateService(repository).execute(
        SetMicLockState(
            ADMIN,
            plate_id,
            str(checked.snapshot.metadata["updated_at"]),
            locked=True,
        )
    )
    with pytest.raises(PermissionError, match="Locked"):
        SoftDeleteMicPlateService(repository).execute(
            SoftDeleteMicPlate(ADMIN, plate_id, str(locked.snapshot.metadata["updated_at"]))
        )
    unlocked = SetMicLockStateService(repository).execute(
        SetMicLockState(
            ADMIN,
            plate_id,
            str(locked.snapshot.metadata["updated_at"]),
            locked=False,
        )
    )
    deleted = SoftDeleteMicPlateService(repository).execute(
        SoftDeleteMicPlate(ADMIN, plate_id, str(unlocked.snapshot.metadata["updated_at"]))
    )
    assert deleted.snapshot.metadata["deleted_at"] is not None
    assert deleted.snapshot.metadata["deleted_by"] == ADMIN.user_id
    assert SearchMicResultsService(repository).execute(SearchMicResults(VIEWER)) == ()
    deleted_results = SearchMicResultsService(repository).execute(
        SearchMicResults(ADMIN, include_deleted=True)
    )
    assert len(deleted_results) == 4
    with pytest.raises(PermissionError):
        SearchMicResultsService(repository).execute(SearchMicResults(VIEWER, include_deleted=True))
    restored = RestoreMicPlateService(repository).execute(
        SoftDeleteMicPlate(ADMIN, plate_id, str(deleted.snapshot.metadata["updated_at"]))
    )
    assert restored.snapshot.metadata["deleted_at"] is None
    assert restored.snapshot.metadata["deleted_by"] is None


def test_indexed_search_pagination_and_conflict(repository: SqlPlateReaderRepository) -> None:
    plate_id = seed_plate(repository)
    service = SearchMicResultsService(repository)

    assert len(service.execute(SearchMicResults(VIEWER, strain="strain_normal"))) == 1
    assert len(service.execute(SearchMicResults(VIEWER, treatment="compound_x", limit=2))) == 2
    assert (
        len(service.execute(SearchMicResults(VIEWER, treatment="compound_x", limit=2, offset=2)))
        == 2
    )
    assert service.execute(SearchMicResults(VIEWER, text="not present")) == ()
    plates = SearchMicPlatesService(repository).execute(SearchRuns(VIEWER, limit=10))
    assert len(plates) == 1
    assert plates[0].plate_id == plate_id
    plan = repository.connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM mic_results WHERE strain = ?",
        ("strain_normal",),
    ).fetchall()
    assert any("idx_mic_results_search" in str(row) for row in plan)

    snapshot = LoadMicPlateService(repository).execute(EDITOR, plate_id).snapshot
    stale = str(snapshot.metadata["updated_at"])
    SetMicReviewStateService(repository).execute(
        SetMicReviewState(EDITOR, plate_id, stale, checked=True)
    )
    with pytest.raises(ConcurrencyConflictError):
        UpdateMicMetadataService(repository).execute(
            UpdateMicMetadata(EDITOR, plate_id, stale, plate_name="stale edit")
        )


def test_search_catalog_selected_metadata_and_custom_filters(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = seed_plate(repository)
    repository.connection.execute(
        "UPDATE wells SET custom_json = ? WHERE plate_id = ? AND position = 'A1'",
        ('{"host":"human","batch":"B-7"}', plate_id),
    )
    snapshot = LoadMicPlateService(repository).execute(VIEWER, plate_id).snapshot
    repository.replace_experiment_tags(snapshot.metadata["experiment_id"], ("infection", "screen"))

    catalog = LoadMicResultSearchCatalogService(repository).execute(VIEWER)
    assert catalog.strains == tuple(sorted(catalog.strains, key=str.casefold))
    assert "compound_x" in catalog.treatments
    assert {field.key for field in catalog.fields}.issuperset(
        {"experiment_date", "plate_name", "mic_value", "custom.host", "custom.batch"}
    )

    rows = SearchMicResultsService(repository).execute(
        MicResultSearchQuery(
            VIEWER,
            strains=("strain_normal",),
            treatments=("compound_x",),
            field_filters=(
                ("custom.host", "hum"),
                ("plate_name", "Plate 1"),
                ("tags", "infect"),
            ),
        )
    )
    assert len(rows) == 1
    assert rows[0]["plate_id"] == plate_id
    assert rows[0]["experiment_name"] == "MIC workflow fixture"
    assert rows[0]["custom.host"] == "human"
    assert rows[0]["custom.batch"] == "B-7"

    with pytest.raises(ValueError, match="Unknown MIC result filter"):
        SearchMicResultsService(repository).execute(
            MicResultSearchQuery(VIEWER, field_filters=(("not-a-field", "value"),))
        )


def test_mic_workflow_rejects_invalid_commands_and_missing_plates(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = seed_plate(repository)
    view = LoadMicPlateService(repository).execute(EDITOR, plate_id)
    updated_at = str(view.snapshot.metadata["updated_at"])

    with pytest.raises(ValueError, match="Unsupported MIC algorithm"):
        ComputeMicRevisionService(repository).execute(
            ComputeMicRevision(EDITOR, plate_id, "mic-endpoint/old", threshold=0.1)
        )
    with pytest.raises(ValueError, match="At least one MIC metadata"):
        UpdateMicMetadataService(repository).execute(
            UpdateMicMetadata(EDITOR, plate_id, updated_at)
        )
    with pytest.raises(ValueError, match="At least one MIC well"):
        UpdateMicLayoutService(repository).execute(
            UpdateMicLayout(EDITOR, plate_id, updated_at, ())
        )
    with pytest.raises(ValueError, match="repeat a well"):
        UpdateMicLayoutService(repository).execute(
            UpdateMicLayout(
                EDITOR,
                plate_id,
                updated_at,
                (MicWellLayoutChange("A1"), MicWellLayoutChange("A1")),
            )
        )
    missing = type(plate_id)("missing-mic-plate")
    with pytest.raises(LookupError, match="not found"):
        LoadMicPlateService(repository).execute(VIEWER, missing)
    with pytest.raises(LookupError, match="not found"):
        LoadMicPlateService(repository).cache_token(VIEWER, missing)
    with pytest.raises(PermissionError, match="admins"):
        SearchMicPlatesService(repository).execute(SearchRuns(VIEWER, include_deleted=True))


def test_mic_layout_can_explicitly_clear_optional_text(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = seed_plate(repository)
    before = LoadMicPlateService(repository).execute(EDITOR, plate_id)
    updated = UpdateMicLayoutService(repository).execute(
        UpdateMicLayout(
            EDITOR,
            plate_id,
            str(before.snapshot.metadata["updated_at"]),
            (
                MicWellLayoutChange(
                    "A1",
                    display_name=" ",
                    strain="",
                    treatment=" ",
                    medium="",
                    notes="",
                    custom_labels={},
                ),
            ),
        )
    )
    row = next(well for well in updated.snapshot.wells if well["position"] == "A1")
    assert row["display_name"] is None
    assert row["strain"] is None
    assert row["treatment"] is None
    assert row["medium"] is None
    assert row["notes"] is None
    assert row["custom_json"] == "{}"


def seed_plate(repository: SqlPlateReaderRepository):
    result = ImportMicPlateService(repository, id_factory=id_sequence("seed")).execute(
        ImportMicPlate(
            actor=EDITOR,
            source_name="plate_cases.csv",
            source_sha256=hashlib.sha256(MIC_CSV.encode()).hexdigest(),
            parser_version=MIC_PLATE_PARSER_VERSION,
            experiment_name="MIC workflow fixture",
            plate_name="MIC Plate 1",
            experiment_date=date(2026, 1, 3),
            threshold=0.1,
        ),
        MIC_CSV,
    )
    return result.plate_id


def id_sequence(prefix: str) -> Callable[[], str]:
    counter = iter(range(1, 100_000))
    return lambda: f"{prefix}-{next(counter):05d}"


def raw_hash(repository: SqlPlateReaderRepository, plate_id: str) -> str:
    rows = repository.connection.execute(
        "SELECT well_id, channel, value_raw FROM mic_readings WHERE plate_id = ? "
        "ORDER BY well_id, channel",
        (plate_id,),
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()
