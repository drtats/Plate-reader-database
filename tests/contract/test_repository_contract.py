from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import turso

from plate_reader.application.contracts import ExperimentId, PlateId
from plate_reader.application.ports import PlateReaderRepository
from plate_reader.application.ports.repositories import ConcentrationRange
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.database.dbapi import Connection
from plate_reader.infrastructure.database.repository import (
    ConcurrencyConflictError,
    InvalidRepositoryValueError,
    RecordNotFoundError,
)
from plate_reader.infrastructure.database.transactions import NestedTransactionError

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
DATABASE_INTEGRITY_ERRORS = (
    sqlite3.IntegrityError,
    turso.IntegrityError,
    turso.DatabaseError,
)


@dataclass(slots=True)
class RepositoryHarness:
    backend: DatabaseBackend
    path: Path
    connection: Connection
    repository: SqlPlateReaderRepository


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RepositoryHarness]:
    backend: DatabaseBackend = request.param
    path = tmp_path / f"{backend.value}.sqlite"
    connection = connect_database(DatabaseConfig(path, backend, MIGRATIONS))
    selected = RepositoryHarness(
        backend=backend,
        path=path,
        connection=connection,
        repository=SqlPlateReaderRepository(connection),
    )
    try:
        yield selected
    finally:
        connection.close()


def test_adapter_satisfies_frozen_repository_protocol(harness: RepositoryHarness) -> None:
    assert isinstance(harness.repository, PlateReaderRepository)
    assert harness.connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (2,)
    assert harness.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_complete_growth_repository_flow(harness: RepositoryHarness) -> None:
    repository = harness.repository
    seed_growth(repository)
    assert repository.source_exists("fixture-growth-key")
    assert repository.plate_for_source("fixture-growth-key") == "plate-growth"
    assert repository.user_by_email("FIXTURE@EXAMPLE.INVALID") == {
        "user_id": "user-1",
        "email": "fixture@example.invalid",
        "display_name": "Fixture User",
        "role": "admin",
        "is_active": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    runs = repository.search_runs({"project": "Contract", "limit": 20, "offset": 0})
    assert len(runs) == 1
    assert runs[0].plate_id == "plate-growth"
    assert runs[0].strains == ("Synthetic strain",)
    assert runs[0].treatments == ("none",)
    assert runs[0].concentration_ranges == (ConcentrationRange(0.0, 0.0, "ug/mL"),)
    filtered = repository.search_runs(
        {"strain": "Synthetic strain", "medium": "Synthetic medium", "treatment": "none"}
    )
    assert len(filtered) == 1
    snapshot = repository.load_plate(PlateId("plate-growth"))
    assert snapshot is not None
    assert snapshot.metadata["assay_type"] == "growth"
    assert len(snapshot.wells) == 2
    assert len(snapshot.raw_observations) == 4
    chunks = tuple(repository.stream_growth_measurements(PlateId("plate-growth"), chunk_size=3))
    assert tuple(len(chunk) for chunk in chunks) == (3, 1)
    with pytest.raises(InvalidRepositoryValueError, match="chunk_size"):
        tuple(repository.stream_growth_measurements(PlateId("plate-growth"), chunk_size=0))


def test_run_library_metadata_is_single_query_normalized_and_paged(
    harness: RepositoryHarness,
) -> None:
    repository = harness.repository
    seed_growth(repository)
    with repository.transaction():
        repository.update_well_layout(
            PlateId("plate-growth"),
            [
                {
                    "position": "A1",
                    "strain": " Alpha ",
                    "medium": " Synthetic medium ",
                    "treatment": " Drug ",
                    "concentration": 0.25,
                    "concentration_unit": "ug/mL",
                },
                {
                    "position": "A2",
                    "strain": "alpha",
                    "medium": "synthetic medium",
                    "treatment": "drug",
                    "concentration": 1.0,
                    "concentration_unit": "ug/mL",
                },
            ],
        )
        repository.insert_wells(
            PlateId("plate-growth"),
            [
                {
                    **well_values("well-a3", "A3", 0, 2),
                    "is_blank": False,
                },
                {
                    **well_values("well-a4", "A4", 0, 3),
                    "is_blank": True,
                },
                {
                    **well_values("well-a5", "A5", 0, 4),
                    "is_blank": False,
                },
            ],
        )
        repository.insert_conditions(
            [
                {
                    "well_id": "well-a3",
                    "strain": "Beta",
                    "medium": "  ",
                    "treatment": "none",
                    "concentration": 4.0,
                    "concentration_unit": "mM",
                },
                {
                    "well_id": "well-a4",
                    "strain": "Hidden",
                    "medium": "Hidden medium",
                    "treatment": "Secret",
                    "concentration": 99.0,
                    "concentration_unit": "mM",
                },
                {
                    "well_id": "well-a5",
                    "strain": "No unit",
                    "medium": None,
                    "treatment": None,
                    "concentration": 5.0,
                    "concentration_unit": "  ",
                },
            ]
        )
        repository.create_plate(
            {
                "plate_id": "plate-without-conditions",
                "experiment_id": "experiment-1",
                "assay_type": "growth",
                "plate_name": "No conditions",
                "created_by": "user-1",
                "created_at": "2026-02-01T00:00:00+00:00",
                "updated_at": "2026-02-01T00:00:00+00:00",
            }
        )

    recorded = RecordingConnection(harness.connection)
    summaries = SqlPlateReaderRepository(recorded).search_runs({"limit": 20, "offset": 0})

    assert len(recorded.statements) == 1
    assert "growth_measurements" not in recorded.statements[0].lower()
    by_plate = {str(summary.plate_id): summary for summary in summaries}
    assert by_plate["plate-growth"].strains == ("Alpha", "Beta", "No unit")
    assert by_plate["plate-growth"].treatments == ("Drug", "none")
    assert by_plate["plate-growth"].concentration_ranges == (
        ConcentrationRange(4.0, 4.0, "mM"),
        ConcentrationRange(0.25, 1.0, "ug/mL"),
        ConcentrationRange(5.0, 5.0, None),
    )
    assert by_plate["plate-without-conditions"].strains == ()
    assert by_plate["plate-without-conditions"].treatments == ()
    assert by_plate["plate-without-conditions"].concentration_ranges == ()
    assert [summary.plate_id for summary in repository.search_runs({"limit": 1, "offset": 0})] == [
        "plate-without-conditions"
    ]
    assert [summary.plate_id for summary in repository.search_runs({"limit": 1, "offset": 1})] == [
        "plate-growth"
    ]
    assert [summary.plate_id for summary in repository.search_runs({"text": "beta"})] == [
        "plate-growth"
    ]
    assert [
        summary.plate_id for summary in repository.search_runs({"text": "synthetic medium"})
    ] == ["plate-growth"]
    assert repository.search_runs({"text": "hidden"}) == ()


class RecordingConnection:
    """Records the one database request issued by the Library projection."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.statements: list[str] = []

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        self.statements.append(sql)
        return self._connection.execute(sql, parameters)


def test_growth_comparison_wells_are_condition_only_and_filter_available_growth_plates(
    harness: RepositoryHarness,
) -> None:
    repository = harness.repository
    seed_growth(repository)
    with repository.transaction():
        repository.create_plate(
            {
                "plate_id": "plate-growth-two",
                "experiment_id": "experiment-1",
                "assay_type": "growth",
                "plate_name": "Second Growth Plate",
                "created_by": "user-1",
            }
        )
        repository.insert_wells(
            PlateId("plate-growth-two"),
            [
                well_values("well-two-b1", "B1", 1, 0),
                well_values("well-two-a1", "A1", 0, 0),
            ],
        )
        repository.insert_conditions(
            [
                {
                    "well_id": "well-two-a1",
                    "strain": "comparison strain",
                    "medium": "comparison medium",
                    "replicate": 2,
                    "treatment": "compound",
                    "concentration": 0.5,
                    "concentration_unit": "ug/mL",
                },
                {"well_id": "well-two-b1"},
            ]
        )
        repository.create_plate(
            {
                "plate_id": "plate-mic-comparison",
                "experiment_id": "experiment-1",
                "assay_type": "mic",
                "plate_name": "MIC comparison plate",
                "created_by": "user-1",
            }
        )

    recorded = RecordingConnection(harness.connection)
    rows = SqlPlateReaderRepository(recorded).growth_comparison_wells(
        (PlateId("plate-growth-two"), PlateId("plate-growth"))
    )

    assert len(recorded.statements) == 1
    assert "growth_measurements" not in recorded.statements[0].lower()
    assert "growth_series_chunks" not in recorded.statements[0].lower()
    assert tuple(rows[0]) == (
        "plate_id",
        "experiment_name",
        "plate_name",
        "well_id",
        "position",
        "strain",
        "treatment",
        "concentration",
        "concentration_unit",
        "medium",
        "replicate",
        "is_blank",
    )
    assert [(row["plate_id"], row["position"]) for row in rows] == [
        ("plate-growth-two", "A1"),
        ("plate-growth-two", "B1"),
        ("plate-growth", "A1"),
        ("plate-growth", "A2"),
    ]
    assert rows[0] == {
        "plate_id": "plate-growth-two",
        "experiment_name": "Synthetic Experiment",
        "plate_name": "Second Growth Plate",
        "well_id": "well-two-a1",
        "position": "A1",
        "strain": "comparison strain",
        "treatment": "compound",
        "concentration": 0.5,
        "concentration_unit": "ug/mL",
        "medium": "comparison medium",
        "replicate": 2,
        "is_blank": 0,
    }
    available = repository.growth_comparison_wells(
        (PlateId("plate-growth"), PlateId("plate-mic-comparison"))
    )
    assert {row["plate_id"] for row in available} == {"plate-growth"}
    with_missing = repository.growth_comparison_wells(
        (PlateId("plate-growth"), PlateId("missing-plate"))
    )
    assert {row["plate_id"] for row in with_missing} == {"plate-growth"}


@pytest.mark.parametrize(
    "plate_ids, message",
    [
        ((PlateId("plate-growth"),), "at least two"),
        ((PlateId("plate-growth"), PlateId("plate-growth")), "must be unique"),
        (tuple(PlateId(f"plate-{index}") for index in range(101)), "at most 100"),
    ],
)
def test_growth_comparison_wells_validate_selection_bounds(
    harness: RepositoryHarness, plate_ids: tuple[PlateId, ...], message: str
) -> None:
    with pytest.raises(InvalidRepositoryValueError, match=message):
        harness.repository.growth_comparison_wells(plate_ids)


def test_import_failure_rolls_back_every_table(harness: RepositoryHarness) -> None:
    repository = harness.repository
    with pytest.raises(RuntimeError, match="forced failure"), repository.transaction():
        repository.upsert_user(user_values())
        repository.create_experiment(experiment_values())
        raise RuntimeError("forced failure")
    assert harness.connection.execute("SELECT count(*) FROM users").fetchone() == (0,)
    assert harness.connection.execute("SELECT count(*) FROM experiments").fetchone() == (0,)


def test_nested_transactions_are_rejected(harness: RepositoryHarness) -> None:
    repository = harness.repository
    with repository.transaction(), pytest.raises(NestedTransactionError):  # noqa: SIM117
        with repository.transaction():
            pass


def test_metadata_updates_do_not_touch_raw_measurements(harness: RepositoryHarness) -> None:
    repository = harness.repository
    seed_growth(repository)
    raw_before = raw_hash(harness.connection, "plate-growth")
    initial = "2026-01-01T00:00:00+00:00"
    with repository.transaction():
        updated_at = repository.update_plate_metadata(
            PlateId("plate-growth"), initial, {"plate_name": "Renamed plate"}
        )
        repository.update_experiment_metadata(
            ExperimentId("experiment-1"), initial, {"name": "Renamed experiment"}
        )
        repository.update_well_layout(
            PlateId("plate-growth"),
            [
                {
                    "position": "A2",
                    "display_name": "Updated A2",
                    "strain": "Updated strain",
                    "concentration": 2.0,
                }
            ],
        )
        repository.append_provenance(
            {
                "event_id": "event-update",
                "actor_id": "user-1",
                "event_type": "metadata_updated",
                "entity_type": "plate",
                "entity_id": "plate-growth",
            }
        )
    assert updated_at != initial
    assert raw_hash(harness.connection, "plate-growth") == raw_before
    assert harness.connection.execute("SELECT count(*) FROM growth_series_chunks").fetchone() == (
        1,
    )
    with pytest.raises(ConcurrencyConflictError):
        repository.update_plate_metadata(
            PlateId("plate-growth"), initial, {"plate_name": "Stale edit"}
        )
    with pytest.raises(RecordNotFoundError):
        repository.update_plate_metadata(PlateId("missing"), initial, {"plate_name": "x"})


def test_raw_immutability_and_foreign_keys_are_enforced(harness: RepositoryHarness) -> None:
    repository = harness.repository
    seed_growth(repository)
    with pytest.raises(DATABASE_INTEGRITY_ERRORS, match="immutable"):
        harness.connection.execute(
            "UPDATE growth_series_chunks SET channel = 'changed' WHERE plate_id = 'plate-growth'"
        )
    with pytest.raises(DATABASE_INTEGRITY_ERRORS), repository.transaction():
        repository.create_plate(
            {
                "plate_id": "orphan",
                "experiment_id": "missing",
                "assay_type": "growth",
                "plate_name": "Orphan",
                "created_by": "user-1",
            }
        )


def test_analysis_revisions_and_derived_results(harness: RepositoryHarness) -> None:
    repository = harness.repository
    seed_growth(repository)
    with repository.transaction():
        first = repository.add_analysis_revision(revision_values("revision-1"))
        repository.insert_growth_backgrounds(
            first,
            [
                {
                    "background_group": "plate",
                    "channel": "od600",
                    "time_index": 0,
                    "elapsed_microseconds": 0,
                    "mean_value": 0.05,
                    "std_value": 0.001,
                    "coefficient_of_variation": 0.02,
                    "blank_count": 2,
                    "qc_status": "good",
                }
            ],
        )
        repository.insert_growth_metrics(
            first,
            [
                {
                    "well_id": "well-a1",
                    "channel": "od600",
                    "metric_name": "auc",
                    "metric_value": 1.2,
                    "metric_unit": "OD*min",
                }
            ],
        )
        second = repository.add_analysis_revision(revision_values("revision-2"))
    assert first == "revision-1"
    assert second == "revision-2"
    assert harness.connection.execute(
        "SELECT revision_id, is_current FROM analysis_revisions ORDER BY revision_id"
    ).fetchall() == [("revision-1", 0), ("revision-2", 1)]


def test_mic_repository_flow(harness: RepositoryHarness) -> None:
    repository = harness.repository
    with repository.transaction():
        repository.upsert_user(user_values())
        repository.create_experiment(experiment_values())
        repository.create_plate(
            {
                "plate_id": "plate-mic",
                "experiment_id": "experiment-1",
                "assay_type": "mic",
                "plate_name": "MIC Plate",
                "threshold": 0.1,
                "created_by": "user-1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
        repository.insert_wells(
            PlateId("plate-mic"),
            [well_values("well-mic-a1", "A1", 0, 0)],
        )
        repository.insert_conditions(
            [
                {
                    "well_id": "well-mic-a1",
                    "strain": "strain",
                    "medium": "medium",
                    "replicate": 1,
                    "treatment": "compound",
                    "concentration": 1.0,
                    "concentration_unit": "ug/mL",
                }
            ]
        )
        repository.insert_raw_observations(
            PlateId("plate-mic"),
            [{"well_id": "well-mic-a1", "channel": "od", "value_raw": 0.2}],
        )
        revision = repository.add_analysis_revision(
            {
                "revision_id": "revision-mic",
                "plate_id": "plate-mic",
                "assay_type": "mic",
                "algorithm_name": "mic_endpoint",
                "algorithm_version": "mic-endpoint/1.0.0",
                "input_sha256": "mic-input",
                "created_by": "user-1",
            }
        )
        repository.insert_mic_well_calls(
            revision,
            [
                {
                    "well_id": "well-mic-a1",
                    "background_value": 0.05,
                    "value_background_subtracted": 0.15,
                    "growth_call": True,
                }
            ],
        )
        repository.insert_mic_results(
            revision,
            [
                {
                    "result_id": "result-1",
                    "group_key": '["strain","compound","medium",1,"ug/mL"]',
                    "strain": "strain",
                    "treatment": "compound",
                    "medium": "medium",
                    "replicate": 1,
                    "mic_value": 1.0,
                    "mic_operator": ">",
                    "mic_unit": "ug/mL",
                    "threshold_used": 0.1,
                    "lowest_tested_concentration": 1.0,
                    "highest_tested_concentration": 1.0,
                    "concentrations_json": [1.0],
                    "point_count": 1,
                }
            ],
        )
    snapshot = repository.load_plate(PlateId("plate-mic"))
    assert snapshot is not None
    assert snapshot.metadata["assay_type"] == "mic"
    assert snapshot.raw_observations[0]["value_raw"] == 0.2
    assert len(snapshot.revisions) == 1


def test_pyturso_file_opens_with_standard_sqlite(harness: RepositoryHarness) -> None:
    if harness.backend is not DatabaseBackend.PYTURSO:
        pytest.skip("pyturso-specific compatibility check")
    seed_growth(harness.repository)
    harness.connection.close()
    standard = sqlite3.connect(harness.path)
    try:
        assert standard.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert standard.execute("SELECT count(*) FROM growth_series_chunks").fetchone() == (1,)
    finally:
        standard.close()


def seed_growth(repository: SqlPlateReaderRepository) -> None:
    with repository.transaction():
        repository.upsert_user(user_values())
        repository.create_experiment(experiment_values())
        repository.create_plate(
            {
                "plate_id": "plate-growth",
                "experiment_id": "experiment-1",
                "assay_type": "growth",
                "plate_name": "Growth Plate",
                "plate_format": 96,
                "channel": "od600",
                "created_by": "user-1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
        repository.insert_wells(
            PlateId("plate-growth"),
            [
                well_values("well-a1", "A1", 0, 0),
                well_values("well-a2", "A2", 0, 1),
            ],
        )
        repository.insert_conditions(
            [
                condition_values("well-a1"),
                condition_values("well-a2"),
            ]
        )
        repository.insert_raw_observations(
            PlateId("plate-growth"),
            [
                measurement_values("well-a1", 0, 0, 0.05),
                measurement_values("well-a2", 0, 0, 0.06),
                measurement_values("well-a1", 1, 600_000_000, 0.10),
                measurement_values("well-a2", 1, 600_000_000, 0.12),
            ],
        )
        repository.record_import_source(
            {
                "source_id": "source-growth",
                "plate_id": "plate-growth",
                "source_kind": "growth_csv",
                "original_filename": "synthetic.csv",
                "content_sha256": "synthetic-hash",
                "byte_size": 100,
                "parser_version": "growth-normalize/1.0.0",
                "idempotency_key": "fixture-growth-key",
                "status": "imported",
                "imported_by": "user-1",
                "imported_at": "2026-01-01T00:00:00+00:00",
            }
        )
        repository.append_provenance(
            {
                "event_id": "event-create",
                "actor_id": "user-1",
                "event_type": "growth_imported",
                "entity_type": "plate",
                "entity_id": "plate-growth",
                "occurred_at": "2026-01-01T00:00:00+00:00",
            }
        )


def user_values() -> dict[str, object]:
    return {
        "user_id": "user-1",
        "email": "fixture@example.invalid",
        "display_name": "Fixture User",
        "role": "admin",
        "is_active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def experiment_values() -> dict[str, object]:
    return {
        "experiment_id": "experiment-1",
        "name": "Synthetic Experiment",
        "project": "Contract",
        "experiment_date": "2026-01-01",
        "operator_name": "Fixture User",
        "tags": ["synthetic", "contract"],
        "created_by": "user-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def well_values(well_id: str, position: str, row: int, column: int) -> dict[str, object]:
    return {
        "well_id": well_id,
        "position": position,
        "row_index": row,
        "column_index": column,
        "display_name": f"Sample {position}",
        "background_group": "plate",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def condition_values(well_id: str) -> dict[str, object]:
    return {
        "well_id": well_id,
        "strain": "Synthetic strain",
        "medium": "Synthetic medium",
        "replicate": 1,
        "treatment": "none",
        "concentration": 0.0,
        "concentration_unit": "ug/mL",
    }


def measurement_values(
    well_id: str, time_index: int, elapsed_microseconds: int, value: float
) -> dict[str, object]:
    return {
        "well_id": well_id,
        "channel": "od600",
        "time_index": time_index,
        "elapsed_microseconds": elapsed_microseconds,
        "value_raw": value,
    }


def revision_values(revision_id: str) -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "plate_id": "plate-growth",
        "assay_type": "growth",
        "algorithm_name": "growth_background",
        "algorithm_version": "growth-background/1.0.0",
        "parameters_json": {"cv_high": 0.1},
        "input_sha256": "raw-hash",
        "created_by": "user-1",
    }


def raw_hash(connection: Connection, plate_id: str) -> str:
    rows = [
        row
        for chunk in SqlPlateReaderRepository(connection).stream_growth_measurements(plate_id)
        for row in chunk
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
