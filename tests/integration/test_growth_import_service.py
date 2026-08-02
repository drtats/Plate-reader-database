from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    GrowthRunMetadata,
    ImportGrowthRun,
    Role,
    UserId,
    WellLayoutChange,
)
from plate_reader.application.services import (
    ImportAuthorizationError,
    ImportGrowthRunService,
    SourceHashMismatchError,
    UnsupportedParserVersionError,
)
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
GROWTH_CSV = (ROOT / "tests" / "fixtures" / "growth" / "with_time.csv").read_text(encoding="utf-8")
LABEL_CSV = (ROOT / "tests" / "fixtures" / "growth" / "labels.csv").read_text(encoding="utf-8")


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"import-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    try:
        yield SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def test_growth_import_is_atomic_complete_and_idempotent(
    repository: SqlPlateReaderRepository,
) -> None:
    service = ImportGrowthRunService(repository, id_factory=id_sequence())
    command = import_command()
    first = service.execute(command, GROWTH_CSV, label_csv_text=LABEL_CSV)
    second = service.execute(command, GROWTH_CSV, label_csv_text=LABEL_CSV)

    assert first.created is True
    assert first.measurement_count == 384
    assert second.created is False
    assert second.plate_id == first.plate_id
    assert second.experiment_id == first.experiment_id
    counts = {
        table: repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "users",
            "experiments",
            "plates",
            "wells",
            "well_conditions",
            "growth_measurements",
            "import_sources",
            "provenance_events",
        )
    }
    counts["growth_measurements"] = sum(
        len(chunk) for chunk in repository.stream_growth_measurements(first.plate_id)
    )
    assert counts == {
        "users": 1,
        "experiments": 1,
        "plates": 1,
        "wells": 96,
        "well_conditions": 96,
        "growth_measurements": 384,
        "import_sources": 1,
        "provenance_events": 1,
    }
    labels = repository.connection.execute(
        "SELECT position, display_name FROM wells WHERE plate_id = ? "
        "AND position IN ('A1', 'B1') ORDER BY position",
        (first.plate_id,),
    ).fetchall()
    assert labels == [("A1", "sample_A1"), ("B1", "sample_B1")]


def test_forced_mid_import_failure_rolls_back_everything(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ImportGrowthRunService(repository, id_factory=id_sequence())

    def fail_after_layout(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced measurement failure")

    monkeypatch.setattr(repository, "insert_raw_observations", fail_after_layout)
    with pytest.raises(RuntimeError, match="forced measurement failure"):
        service.execute(import_command(), GROWTH_CSV)
    for table in (
        "users",
        "experiments",
        "plates",
        "wells",
        "well_conditions",
        "growth_measurements",
        "import_sources",
        "provenance_events",
    ):
        assert repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_source_hash_parser_and_command_role_are_validated_before_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    service = ImportGrowthRunService(repository, id_factory=id_sequence())
    with pytest.raises(SourceHashMismatchError):
        service.execute(import_command(source_sha256="0" * 64), GROWTH_CSV)
    with pytest.raises(UnsupportedParserVersionError):
        service.execute(import_command(parser_version="legacy/0"), GROWTH_CSV)
    with pytest.raises(ImportAuthorizationError):
        service.execute(import_command(role=Role.VIEWER), GROWTH_CSV)
    assert repository.connection.execute("SELECT count(*) FROM users").fetchone() == (0,)


def test_stored_role_and_identity_cannot_be_elevated_by_command(
    repository: SqlPlateReaderRepository,
) -> None:
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": "stored-user",
                "email": "editor@example.invalid",
                "display_name": "Stored Viewer",
                "role": "viewer",
                "is_active": True,
            }
        )
    service = ImportGrowthRunService(repository, id_factory=id_sequence())
    with pytest.raises(ImportAuthorizationError, match="Stored user role"):
        service.execute(import_command(user_id="stored-user"), GROWTH_CSV)
    stored = repository.user_by_email("editor@example.invalid")
    assert stored is not None
    assert stored["role"] == "viewer"
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)


def test_growth_import_commits_layout_and_conditions_atomically(
    repository: SqlPlateReaderRepository,
) -> None:
    result = ImportGrowthRunService(repository, id_factory=id_sequence()).execute(
        import_command(),
        GROWTH_CSV,
        layout_changes=(
            WellLayoutChange(
                position="A1",
                display_name="blank A1",
                is_blank=True,
                background_group="media",
                medium="M9",
                replicate=2,
            ),
        ),
    )

    row = repository.connection.execute(
        "SELECT w.display_name, w.is_blank, w.background_group, wc.medium, wc.replicate "
        "FROM wells w JOIN well_conditions wc ON wc.well_id = w.well_id "
        "WHERE w.plate_id = ? AND w.position = 'A1'",
        (result.plate_id,),
    ).fetchone()
    assert row == ("blank A1", 1, "media", "M9", 2)


def test_growth_import_preserves_rich_legacy_metadata_and_well_details(
    repository: SqlPlateReaderRepository,
) -> None:
    result = ImportGrowthRunService(repository, id_factory=id_sequence()).execute(
        import_command(),
        GROWTH_CSV,
        metadata=GrowthRunMetadata(
            project="Project Alpha",
            tags=("biofilm", "kinetics"),
            operator_name="Researcher A",
            instrument="Epoch 2",
            channel="OD620",
            temperature=37.0,
            temperature_unit="C",
            measurement_type="OD600",
            manual_subtraction=0.012,
            notes="rich metadata",
            experiment_custom_json={"batch": "B1"},
            plate_custom_json={"lid": True},
        ),
        layout_changes=(
            WellLayoutChange(
                position="A1",
                plot_selected=True,
                notes="well note",
                grouping_label="group one",
                inoculum_size=0.02,
                inoculum_unit="OD600",
                custom_fields={"oxygen": "low", "t0_added_min": 5.0},
            ),
        ),
    )

    metadata = repository.connection.execute(
        "SELECT e.project, e.operator_name, e.notes, e.custom_json, p.instrument, "
        "p.channel, p.temperature, p.temperature_unit, p.manual_subtraction, p.custom_json "
        "FROM experiments e JOIN plates p ON p.experiment_id = e.experiment_id "
        "WHERE p.plate_id = ?",
        (result.plate_id,),
    ).fetchone()
    assert metadata is not None
    assert metadata[:3] == ("Project Alpha", "Researcher A", "rich metadata")
    assert '"batch":"B1"' in metadata[3]
    assert metadata[4:9] == ("Epoch 2", "OD620", 37.0, "C", 0.012)
    assert '"measurement_type":"OD600"' in metadata[9]
    tags = repository.connection.execute("SELECT tag FROM experiment_tags ORDER BY tag").fetchall()
    assert tags == [("biofilm",), ("kinetics",)]
    well = repository.connection.execute(
        "SELECT w.plot_selected, w.notes, w.custom_json, wc.grouping_label, "
        "wc.inoculum_size, wc.inoculum_unit FROM wells w "
        "JOIN well_conditions wc ON wc.well_id = w.well_id WHERE w.position = 'A1'"
    ).fetchone()
    assert well == (
        1,
        "well note",
        '{"oxygen":"low","t0_added_min":5.0}',
        "group one",
        0.02,
        "OD600",
    )


def test_duplicate_layout_change_is_rejected_before_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    duplicate = WellLayoutChange(position="A1")
    with pytest.raises(ValueError, match="Duplicate layout"):
        ImportGrowthRunService(repository, id_factory=id_sequence()).execute(
            import_command(), GROWTH_CSV, layout_changes=(duplicate, duplicate)
        )
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)


def import_command(
    *,
    source_sha256: str | None = None,
    parser_version: str = GROWTH_NORMALIZATION_VERSION,
    role: Role = Role.EDITOR,
    user_id: str = "user-editor",
) -> ImportGrowthRun:
    return ImportGrowthRun(
        actor=Actor(UserId(user_id), "editor@example.invalid", role),
        source_name="synthetic.csv",
        source_sha256=source_sha256 or hashlib.sha256(GROWTH_CSV.encode()).hexdigest(),
        parser_version=parser_version,
        experiment_name="Synthetic imported experiment",
        plate_name="Synthetic plate",
        experiment_date=date(2026, 1, 2),
    )


def id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"generated-{next(counter):04d}"
