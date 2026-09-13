from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    ExperimentId,
    GrowthRunMetadata,
    ImportGrowthRun,
    PlateId,
    Role,
    UserId,
    WellLayoutChange,
)
from plate_reader.application.services.growth_cultivation import (
    CultivationAssignment,
    SaveGrowthCultivationsService,
    json_object,
    suggested_cultivation_experiment_code,
)
from plate_reader.application.services.growth_import import ImportGrowthRunService
from plate_reader.application.services.growth_tabular_export import export_growth_tabular_data
from plate_reader.application.services.growth_workflow import GrowthRunView
from plate_reader.domain.common import DomainValidationError
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
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
ACTOR = Actor(UserId("cultivation-editor"), "cultivation@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"cultivation-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    try:
        yield SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def test_cultivations_persist_reload_and_preserve_raw_and_other_metadata(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    before = required_snapshot(repository, plate_id)
    raw_before = raw_hash(before)

    saved = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        plate_id,
        str(before.metadata["updated_at"]),
        {
            "Team_Code": "PN",
            "CultivationSystemCode": "BRV",
            "Project": "manual registry",
            "InoculationDateTime": "2026-09-12T08:30:00-04:00",
        },
        (CultivationAssignment("A1", "2"), CultivationAssignment("A2", "23")),
    )

    assert raw_hash(saved) == raw_before
    plate_custom = json_object(saved.metadata["plate_custom_json"])
    assert plate_custom["reader_meta"] == "kept"
    assert plate_custom["cultivation_registry"] == {
        "Team_Code": "PN",
        "CultivationSystemCode": "BRV",
        "Project": "manual registry",
        "InoculationDateTime": "2026-09-12T08:30:00-04:00",
    }
    a1 = well(saved, "A1")
    a2 = well(saved, "A2")
    assert json_object(a1["custom_json"]) == {
        "oxygen": "low",
        "Cultivation": "PN-EXP-J3-BRV002R1",
        "Team_Code": "PN",
        "CultivationSystemCode": "BRV",
        "CultivationRun": "002",
    }
    assert json_object(a2["custom_json"])["Cultivation"] == ("PN-EXP-MG1655-BRV023R2")
    assert a1["strain"] == "J3"
    assert a1["replicate"] == 1
    assert repository.connection.execute(
        "SELECT count(*) FROM growth_measurements WHERE plate_id = ?", (plate_id,)
    ).fetchone() == (0,)
    assert sum(len(chunk) for chunk in repository.stream_growth_measurements(plate_id)) == 384
    event = repository.connection.execute(
        "SELECT event_type, details_json FROM provenance_events "
        "WHERE entity_id = ? ORDER BY occurred_at DESC LIMIT 1",
        (plate_id,),
    ).fetchone()
    assert event is not None and event[0] == "cultivation_metadata_updated"
    assert json.loads(event[1])["positions"] == ["A1", "A2"]


def test_registry_only_save_preserves_existing_per_well_components(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    first = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {"Team_Code": "TEAM1", "CultivationSystemCode": "SYS"},
        (CultivationAssignment("A1", "9"),),
    )
    saved_components = json_object(well(first, "A1")["custom_json"])

    second = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        plate_id,
        str(first.metadata["updated_at"]),
        {
            "Team_Code": "TEAM2",
            "CultivationSystemCode": "SYSA",
            "Project": "new shared defaults",
        },
        (),
    )

    assert json_object(well(second, "A1")["custom_json"]) == saved_components
    assert json_object(second.metadata["plate_custom_json"])["cultivation_registry"] == {
        "Team_Code": "TEAM2",
        "CultivationSystemCode": "SYSA",
        "Project": "new shared defaults",
    }


def test_pattern_assignments_persist_per_well_while_shared_defaults_change(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    raw_before = raw_hash(initial)
    service = SaveGrowthCultivationsService(repository)
    first = service.execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {
            "Team_Code": "PN",
            "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
            "CultivationExperimentCode": "1",
        },
        (
            CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "1"),
            CultivationAssignment("A2", "", DEFAULT_CULTIVATION_PATTERN, "1"),
        ),
    )
    a1_before = json_object(well(first, "A1")["custom_json"])
    a2_before = json_object(well(first, "A2")["custom_json"])
    assert a1_before["Cultivation"] == "PN-EXP-J3-001-A01-R1"
    assert a2_before["Cultivation"] == "PN-EXP-MG1655-001-A02-R2"
    assert a1_before["CultivationIDPattern"] == DEFAULT_CULTIVATION_PATTERN
    assert a1_before["CultivationExperimentCode"] == "001"
    assert (
        json_object(first.metadata["plate_custom_json"])["cultivation_registry"][
            "CultivationExperimentCode"
        ]
        == "001"
    )
    assert a1_before["oxygen"] == "low"
    bundle = export_growth_tabular_data((GrowthRunView(first, (), ()),))
    metadata_rows = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    observation_rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    by_id = {row["Cultivation"]: row for row in metadata_rows if row["Cultivation"]}
    assert set(by_id) == {a1_before["Cultivation"], a2_before["Cultivation"]}
    assert observation_rows
    assert {row["Well"] for row in observation_rows} >= {"A1", "A2"}
    for row in observation_rows:
        if row["Well"] in {"A1", "A2"}:
            assert row["Cultivation ID"] in by_id
            assert row["Cultivation experiment code"] == "001"
            assert by_id[row["Cultivation ID"]]["CultivationExperimentCode"] == "001"

    second = service.execute(
        ACTOR,
        plate_id,
        str(first.metadata["updated_at"]),
        {
            "Team_Code": "OTHER",
            "CultivationIDPattern": "{team}-{well}",
            "CultivationExperimentCode": "later",
        },
        (),
    )
    assert json_object(well(second, "A1")["custom_json"]) == a1_before
    assert json_object(well(second, "A2")["custom_json"]) == a2_before
    assert raw_hash(second) == raw_before
    assert sum(len(chunk) for chunk in repository.stream_growth_measurements(plate_id)) == 384


def test_pattern_well_can_be_regenerated_as_legacy_without_stale_pattern_metadata(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    service = SaveGrowthCultivationsService(repository)
    first = service.execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {"Team_Code": "PN"},
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "001"),),
    )
    second = service.execute(
        ACTOR,
        plate_id,
        str(first.metadata["updated_at"]),
        {
            "Team_Code": "PN",
            "CultivationSystemCode": "BRV",
            "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
            "CultivationExperimentCode": "002",
        },
        (CultivationAssignment("A1", "2"),),
    )
    a1 = json_object(well(second, "A1")["custom_json"])
    assert a1["Cultivation"] == "PN-EXP-J3-BRV002R1"
    assert "CultivationIDPattern" not in a1
    assert "CultivationExperimentCode" not in a1


def test_pattern_and_legacy_ids_coexist_without_rewriting_unassigned_wells(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    service = SaveGrowthCultivationsService(repository)
    first = service.execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationSystemCode": "BRV"},
        (CultivationAssignment("A1", "2"),),
    )
    legacy_custom = json_object(well(first, "A1")["custom_json"])
    second = service.execute(
        ACTOR,
        plate_id,
        str(first.metadata["updated_at"]),
        {
            "Team_Code": "PN",
            "CultivationIDPattern": DEFAULT_CULTIVATION_PATTERN,
            "CultivationExperimentCode": "001",
        },
        (CultivationAssignment("A2", "", DEFAULT_CULTIVATION_PATTERN, "001"),),
    )
    assert json_object(well(second, "A1")["custom_json"]) == legacy_custom
    assert json_object(well(second, "A2")["custom_json"])["Cultivation"] == (
        "PN-EXP-MG1655-001-A02-R2"
    )


def test_sequential_suggestion_and_stale_number_conflict_are_transactional(
    repository: SqlPlateReaderRepository,
) -> None:
    first_plate_id = import_run(repository)
    second_plate_id = import_run(repository, unique=2)
    first_before = required_snapshot(repository, first_plate_id)
    second_before = required_snapshot(repository, second_plate_id)
    service = SaveGrowthCultivationsService(repository)
    stale_suggestion = suggested_cultivation_experiment_code(repository)
    assert stale_suggestion == "001"

    first = service.execute(
        ACTOR,
        first_plate_id,
        str(first_before.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationExperimentCode": stale_suggestion},
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, stale_suggestion),),
    )
    assert json_object(well(first, "A1")["custom_json"])["CultivationExperimentCode"] == "001"
    assert suggested_cultivation_experiment_code(repository) == "002"

    second_events_before = provenance_count(repository, second_plate_id)
    with pytest.raises(DomainValidationError, match="refresh the suggestion"):
        service.execute(
            ACTOR,
            second_plate_id,
            str(second_before.metadata["updated_at"]),
            {"Team_Code": "PN", "CultivationExperimentCode": "0001"},
            (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "0001"),),
        )
    second_after_failure = required_snapshot(repository, second_plate_id)
    assert (
        second_after_failure.metadata["plate_custom_json"]
        == second_before.metadata["plate_custom_json"]
    )
    assert (
        well(second_after_failure, "A1")["custom_json"] == well(second_before, "A1")["custom_json"]
    )
    assert provenance_count(repository, second_plate_id) == second_events_before

    second = service.execute(
        ACTOR,
        second_plate_id,
        str(second_before.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationExperimentCode": "002"},
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "002"),),
    )
    assert json_object(well(second, "A1")["custom_json"])["CultivationExperimentCode"] == "002"
    assert suggested_cultivation_experiment_code(repository) == "003"

    experiment_id = ExperimentId(str(first.metadata["experiment_id"]))
    experiment_row = repository.connection.execute(
        "SELECT updated_at FROM experiments WHERE experiment_id = ?", (experiment_id,)
    ).fetchone()
    assert experiment_row is not None
    with repository.transaction():
        repository.update_experiment_metadata(
            experiment_id, str(experiment_row[0]), {"experiment_date": "2027-01-01"}
        )
    reused = service.execute(
        ACTOR,
        first_plate_id,
        str(first.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationExperimentCode": "001"},
        (),
    )
    assert suggested_cultivation_experiment_code(repository) == "003"
    assert json_object(well(reused, "A1")["custom_json"])["CultivationExperimentCode"] == "001"

    with repository.transaction():
        repository.update_plate_metadata(
            first_plate_id,
            str(reused.metadata["updated_at"]),
            {"deleted_at": "2027-01-02T00:00:00", "deleted_by": str(ACTOR.user_id)},
        )
    assert suggested_cultivation_experiment_code(repository) == "003"
    third_plate_id = import_run(repository, unique=3)
    third_before = required_snapshot(repository, third_plate_id)
    with pytest.raises(DomainValidationError, match="refresh the suggestion"):
        service.execute(
            ACTOR,
            third_plate_id,
            str(third_before.metadata["updated_at"]),
            {"Team_Code": "PN", "CultivationExperimentCode": "001"},
            (),
        )


def test_provenance_preserves_previous_and_replacement_cultivation_ids(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    service = SaveGrowthCultivationsService(repository)
    first = service.execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationSystemCode": "BRV"},
        (CultivationAssignment("A1", "2"),),
    )
    service.execute(
        ACTOR,
        plate_id,
        str(first.metadata["updated_at"]),
        {
            "Team_Code": "PN",
            "CultivationSystemCode": "MP96A",
            "Objective": "replacement",
        },
        (CultivationAssignment("A1", "23"),),
    )

    details = [
        json.loads(row[0])
        for row in repository.connection.execute(
            "SELECT details_json FROM provenance_events "
            "WHERE entity_id = ? AND event_type = 'cultivation_metadata_updated' "
            "ORDER BY occurred_at",
            (plate_id,),
        ).fetchall()
    ]
    assert details[0]["registry"] == {
        "before": {},
        "after": {"Team_Code": "PN", "CultivationSystemCode": "BRV"},
    }
    assert details[0]["cultivations"] == [
        {"position": "A1", "before": None, "after": "PN-EXP-J3-BRV002R1"}
    ]
    assert details[1]["registry"] == {
        "before": {"Team_Code": "PN", "CultivationSystemCode": "BRV"},
        "after": {
            "Team_Code": "PN",
            "CultivationSystemCode": "MP96A",
            "Objective": "replacement",
        },
    }
    assert details[1]["cultivations"] == [
        {
            "position": "A1",
            "before": "PN-EXP-J3-BRV002R1",
            "after": "PN-EXP-J3-MP96A023R1",
        }
    ]


def test_save_rejects_stale_token_and_viewer_without_partial_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    service = SaveGrowthCultivationsService(repository)
    saved = service.execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {"Team_Code": "TEAM", "CultivationSystemCode": "SYS"},
        (CultivationAssignment("A1", "1"),),
    )
    events_before = provenance_count(repository, plate_id)

    with pytest.raises(RuntimeError, match="changed since"):
        service.execute(
            ACTOR,
            plate_id,
            str(initial.metadata["updated_at"]),
            {"Team_Code": "STALE", "CultivationSystemCode": "SYSA"},
            (CultivationAssignment("A2", "2"),),
        )
    viewer = Actor(ACTOR.user_id, ACTOR.email, Role.VIEWER)
    with pytest.raises(PermissionError, match="requires one of"):
        service.execute(
            viewer,
            plate_id,
            str(saved.metadata["updated_at"]),
            {"Team_Code": "VIEWER", "CultivationSystemCode": "SYSB"},
            (),
        )

    reloaded = required_snapshot(repository, plate_id)
    assert json_object(reloaded.metadata["plate_custom_json"])["cultivation_registry"] == {
        "Team_Code": "TEAM",
        "CultivationSystemCode": "SYS",
    }
    assert "Cultivation" not in json_object(well(reloaded, "A2")["custom_json"])
    assert provenance_count(repository, plate_id) == events_before


def test_save_rejects_duplicate_with_unassigned_existing_id(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository, duplicate_components=True)
    initial = required_snapshot(repository, plate_id)
    first = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        plate_id,
        str(initial.metadata["updated_at"]),
        {"Team_Code": "TEAM", "CultivationSystemCode": "SYS"},
        (CultivationAssignment("A1", "7"),),
    )

    with pytest.raises(DomainValidationError, match="Duplicate cultivation ID"):
        SaveGrowthCultivationsService(repository).execute(
            ACTOR,
            plate_id,
            str(first.metadata["updated_at"]),
            {"Team_Code": "TEAM", "CultivationSystemCode": "SYS"},
            (CultivationAssignment("A2", "7"),),
        )

    assert "Cultivation" not in json_object(
        well(required_snapshot(repository, plate_id), "A2")["custom_json"]
    )


def test_forced_well_failure_rolls_back_registry_wells_and_provenance(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    plate_id = import_run(repository)
    initial = required_snapshot(repository, plate_id)
    initial_plate_custom = initial.metadata["plate_custom_json"]
    initial_a1_custom = well(initial, "A1")["custom_json"]
    events_before = provenance_count(repository, plate_id)

    def fail_well_update(_plate_id: PlateId, _changes: object) -> None:
        raise RuntimeError("forced cultivation well failure")

    monkeypatch.setattr(repository, "update_well_layout", fail_well_update)
    with pytest.raises(RuntimeError, match="forced cultivation well failure"):
        SaveGrowthCultivationsService(repository).execute(
            ACTOR,
            plate_id,
            str(initial.metadata["updated_at"]),
            {"Team_Code": "TEAM", "CultivationSystemCode": "SYS"},
            (CultivationAssignment("A1", "1"),),
        )

    reloaded = required_snapshot(repository, plate_id)
    assert reloaded.metadata["plate_custom_json"] == initial_plate_custom
    assert well(reloaded, "A1")["custom_json"] == initial_a1_custom
    assert provenance_count(repository, plate_id) == events_before


def import_run(
    repository: SqlPlateReaderRepository, *, duplicate_components: bool = False, unique: int = 1
) -> PlateId:
    a2_strain = "J3" if duplicate_components else "MG1655"
    a2_replicate = 1 if duplicate_components else 2
    result = ImportGrowthRunService(repository, id_factory=id_sequence(unique)).execute(
        ImportGrowthRun(
            actor=ACTOR,
            source_name=f"cultivation-{unique}.csv",
            source_sha256=hashlib.sha256(CSV_TEXT.encode()).hexdigest(),
            parser_version=GROWTH_NORMALIZATION_VERSION,
            idempotency_key=f"cultivation-integration-{unique}",
            experiment_name="Cultivation integration",
            plate_name="Plate 1",
            experiment_date=date(2026, 9, 12),
        ),
        CSV_TEXT,
        metadata=GrowthRunMetadata(plate_custom_json={"reader_meta": "kept"}),
        layout_changes=(
            WellLayoutChange(
                position="A1",
                strain="J3",
                replicate=1,
                custom_fields={"oxygen": "low"},
            ),
            WellLayoutChange(position="A2", strain=a2_strain, replicate=a2_replicate),
        ),
    )
    return result.plate_id


def id_sequence(unique: int = 1) -> Callable[[], str]:
    values = iter(range((unique - 1) * 1000, unique * 1000))
    return lambda: f"cultivation-{next(values):04d}"


def required_snapshot(repository: SqlPlateReaderRepository, plate_id: PlateId):
    snapshot = repository.load_plate(plate_id)
    assert snapshot is not None
    return snapshot


def well(snapshot: object, position: str) -> dict[str, object]:
    return next(item for item in snapshot.wells if item["position"] == position)


def raw_hash(snapshot: object) -> str:
    return hashlib.sha256(repr(snapshot.raw_observations).encode()).hexdigest()


def provenance_count(repository: SqlPlateReaderRepository, plate_id: PlateId) -> int:
    return int(
        repository.connection.execute(
            "SELECT count(*) FROM provenance_events WHERE entity_id = ?", (plate_id,)
        ).fetchone()[0]
    )
