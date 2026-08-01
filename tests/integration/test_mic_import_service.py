from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    ImportMicPlate,
    MicExperimentMetadata,
    Role,
    UserId,
)
from plate_reader.application.services.authorization import AuthorizationError
from plate_reader.application.services.growth_import import (
    SourceHashMismatchError,
    UnsupportedParserVersionError,
)
from plate_reader.application.services.mic_import import (
    ImportMicPlateService,
    PreviewMicPlateService,
)
from plate_reader.domain.common import DomainValidationError
from plate_reader.domain.mic import MIC_PLATE_PARSER_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MIC_CSV = (ROOT / "tests" / "fixtures" / "mic" / "plate_cases.csv").read_text(encoding="utf-8")
ACTOR = Actor(UserId("mic-editor"), "mic-editor@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"mic-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": ACTOR.user_id,
                "email": ACTOR.email,
                "display_name": "MIC Editor",
                "role": ACTOR.role,
                "is_active": True,
            }
        )
    try:
        yield repository
    finally:
        connection.close()


def test_mic_preview_matches_golden_fixture() -> None:
    preview = PreviewMicPlateService().execute(MIC_CSV, 0.1)

    assert preview.source_sha256 == hashlib.sha256(MIC_CSV.encode()).hexdigest()
    assert preview.well_count == 96
    assert preview.blank_count == 80
    assert preview.group_count == preview.result_count == 4
    assert preview.background_value == pytest.approx(0.05)
    assert any(issue.code == "growth_bounce" for issue in preview.issues)


def test_mic_import_is_atomic_complete_and_idempotent(
    repository: SqlPlateReaderRepository,
) -> None:
    service = ImportMicPlateService(repository, id_factory=id_sequence())
    command = import_command()

    first = service.execute(
        command,
        MIC_CSV,
        metadata=MicExperimentMetadata(
            operator_name="fixture-user",
            reader="Synthetic reader",
            incubation_time_hours=18,
            inoculum_od=0.01,
            growth_phase="Exponential",
            harvest_od=0.5,
            doubling_time_minutes=30,
            notes="synthetic",
            custom_json={"study": "MIC fixture"},
        ),
    )
    second = service.execute(command, MIC_CSV)

    assert first.created is True
    assert first.well_count == 96
    assert first.result_count == 4
    assert second.created is False
    assert second.plate_id == first.plate_id
    assert second.revision_id == first.revision_id
    assert second.result_count == 4
    assert table_counts(repository) == {
        "users": 1,
        "experiments": 1,
        "plates": 1,
        "wells": 96,
        "well_conditions": 96,
        "mic_readings": 96,
        "analysis_revisions": 1,
        "mic_well_calls": 96,
        "mic_results": 4,
        "import_sources": 1,
        "provenance_events": 1,
    }
    metadata = repository.connection.execute(
        "SELECT e.operator_name, e.reader, e.incubation_time_hours, e.inoculum_od, "
        "e.growth_phase, e.harvest_od, e.doubling_time_minutes, e.notes, p.threshold, "
        "p.threshold_method, p.background_method, p.is_locked, p.is_checked, p.deleted_at "
        "FROM experiments e JOIN plates p ON p.experiment_id = e.experiment_id"
    ).fetchone()
    assert metadata == (
        "fixture-user",
        "Synthetic reader",
        18.0,
        0.01,
        "Exponential",
        0.5,
        30.0,
        "synthetic",
        0.1,
        "fixed",
        "average_blanks",
        0,
        0,
        None,
    )
    results = repository.connection.execute(
        "SELECT strain, mic_operator, mic_value, warning FROM mic_results ORDER BY strain"
    ).fetchall()
    assert results == [
        ("strain_all_growth", ">", 4.0, None),
        ("strain_all_no_growth", "<=", 0.5, None),
        (
            "strain_bounce",
            "=",
            1.0,
            "Growth bounce detected at 2.0 after no-growth at 1.0",
        ),
        ("strain_normal", "=", 2.0, None),
    ]
    a1 = repository.connection.execute(
        "SELECT mr.value_raw, mwc.background_value, mwc.value_background_subtracted, "
        "mwc.growth_call, wc.strain, wc.treatment, wc.concentration, wc.medium "
        "FROM wells w JOIN mic_readings mr ON mr.well_id = w.well_id "
        "JOIN mic_well_calls mwc ON mwc.well_id = w.well_id "
        "JOIN well_conditions wc ON wc.well_id = w.well_id WHERE w.position = 'A1'"
    ).fetchone()
    assert a1 == (
        0.25,
        pytest.approx(0.05),
        pytest.approx(0.2),
        1,
        "strain_normal",
        "compound_x",
        0.5,
        "Synthetic medium",
    )


def test_forced_mic_import_failure_rolls_back_every_table(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced MIC result failure")

    monkeypatch.setattr(repository, "insert_mic_results", fail)
    with pytest.raises(RuntimeError, match="forced MIC result failure"):
        ImportMicPlateService(repository, id_factory=id_sequence()).execute(
            import_command(), MIC_CSV
        )

    assert table_counts(repository) == {
        "users": 1,
        "experiments": 0,
        "plates": 0,
        "wells": 0,
        "well_conditions": 0,
        "mic_readings": 0,
        "analysis_revisions": 0,
        "mic_well_calls": 0,
        "mic_results": 0,
        "import_sources": 0,
        "provenance_events": 0,
    }


def test_mic_import_validates_authorization_hash_version_and_full_plate(
    repository: SqlPlateReaderRepository,
) -> None:
    service = ImportMicPlateService(repository)
    viewer = ImportMicPlate(
        actor=Actor(ACTOR.user_id, ACTOR.email, Role.VIEWER),
        source_name="mic.csv",
        source_sha256=hashlib.sha256(MIC_CSV.encode()).hexdigest(),
        parser_version=MIC_PLATE_PARSER_VERSION,
        experiment_name="MIC",
        plate_name="Plate",
        experiment_date=date(2026, 1, 3),
        threshold=0.1,
    )
    with pytest.raises(AuthorizationError):
        service.execute(viewer, MIC_CSV)
    with pytest.raises(SourceHashMismatchError):
        service.execute(import_command(source_sha256="0" * 64), MIC_CSV)
    with pytest.raises(UnsupportedParserVersionError):
        service.execute(import_command(parser_version="mic/old"), MIC_CSV)
    partial = "\n".join(MIC_CSV.splitlines()[:-1]) + "\n"
    with pytest.raises(DomainValidationError, match="missing_wells"):
        service.execute(
            import_command(source_sha256=hashlib.sha256(partial.encode()).hexdigest()), partial
        )
    assert repository.connection.execute("SELECT count(*) FROM plates").fetchone() == (0,)


def import_command(
    *,
    source_sha256: str | None = None,
    parser_version: str = MIC_PLATE_PARSER_VERSION,
) -> ImportMicPlate:
    return ImportMicPlate(
        actor=ACTOR,
        source_name="plate_cases.csv",
        source_sha256=source_sha256 or hashlib.sha256(MIC_CSV.encode()).hexdigest(),
        parser_version=parser_version,
        experiment_name="Synthetic MIC experiment",
        plate_name="Synthetic MIC plate",
        experiment_date=date(2026, 1, 3),
        threshold=0.1,
    )


def id_sequence() -> Callable[[], str]:
    counter = iter(range(1, 10_000))
    return lambda: f"mic-generated-{next(counter):04d}"


def table_counts(repository: SqlPlateReaderRepository) -> dict[str, int]:
    return {
        table: int(
            str(repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        )
        for table in (
            "users",
            "experiments",
            "plates",
            "wells",
            "well_conditions",
            "mic_readings",
            "analysis_revisions",
            "mic_well_calls",
            "mic_results",
            "import_sources",
            "provenance_events",
        )
    }
