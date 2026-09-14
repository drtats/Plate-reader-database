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
    prepare_condition_replicate_assignments,
    preview_cultivations,
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
    assert {row["Well Row"] + row["Well Column"] for row in observation_rows} >= {"A1", "A2"}
    for row in observation_rows:
        if row["Well Row"] + row["Well Column"] in {"A1", "A2"}:
            assert row["Cultivation ID"] in by_id
            assert by_id[row["Cultivation ID"]]["Cultivation experiment code"] == "001"
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


def test_matching_wells_on_two_plates_receive_condition_r1_and_r2_with_local_r1(
    repository: SqlPlateReaderRepository,
) -> None:
    first_plate = import_run(repository, unique=1, run_date=date(2026, 8, 12), medium="LB")
    second_plate = import_run(repository, unique=2, run_date=date(2026, 9, 12), medium="LB")
    first_before = required_snapshot(repository, first_plate)
    second_before = required_snapshot(repository, second_plate)
    raw_before = {first_plate: raw_hash(first_before), second_plate: raw_hash(second_before)}
    registry = {"Team_Code": "PN", "CultivationReplicateMode": "condition"}
    first_assignment = prepare_condition_replicate_assignments(
        repository,
        first_before,
        registry,
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "001"),),
    )
    second_assignment = prepare_condition_replicate_assignments(
        repository,
        second_before,
        registry,
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "002"),),
    )
    assert first_assignment[0].cultivation_replicate == 1
    assert second_assignment[0].cultivation_replicate == 2
    second_preview = preview_cultivations(second_before, registry, second_assignment)[0]
    assert second_preview["Replicate"] == 2
    assert second_preview["LocalReplicate"] == 1
    assert second_preview["MatchingWells"] == 2
    assert second_preview["MatchingPlates"] == 2
    assert second_preview["Cultivation"] == "PN-EXP-J3-002-A01-R2"

    service = SaveGrowthCultivationsService(repository)
    first_saved = service.execute(
        ACTOR,
        first_plate,
        str(first_before.metadata["updated_at"]),
        registry,
        first_assignment,
    )
    second_saved = service.execute(
        ACTOR,
        second_plate,
        str(second_before.metadata["updated_at"]),
        registry,
        second_assignment,
    )
    assert json_object(well(first_saved, "A1")["custom_json"])["CultivationReplicate"] == 1
    second_custom = json_object(well(second_saved, "A1")["custom_json"])
    assert second_custom["Cultivation"] == "PN-EXP-J3-002-A01-R2"
    assert second_custom["CultivationReplicate"] == 2
    assert second_custom["LocalReplicate"] == 1
    assert second_custom["CultivationConditionKey"] == second_assignment[0].condition_key
    assert well(second_saved, "A1")["replicate"] == 1
    assert raw_hash(first_saved) == raw_before[first_plate]
    assert raw_hash(second_saved) == raw_before[second_plate]


def test_explicit_primary_concentration_clear_disables_legacy_fallback(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository, medium="LB")
    original = required_snapshot(repository, plate_id)
    raw_before = raw_hash(original)
    well_id = str(well(original, "A1")["well_id"])
    legacy_custom = json_object(well(original, "A1")["custom_json"])
    legacy_custom.update(
        {
            "treatment_1": "Drug A",
            "conc_1": "2",
            "unit_1": "mg/L",
            "treatment_2": "Drug B",
            "conc_2": "5",
            "unit_2": "mg/L",
        }
    )
    # Model a legacy row whose dose exists only in well JSON.
    with repository.transaction():
        repository.connection.execute(
            "UPDATE wells SET custom_json = ? WHERE well_id = ?",
            (json.dumps(legacy_custom), well_id),
        )
    registry = {"Team_Code": "PN", "CultivationReplicateMode": "condition"}
    before = required_snapshot(repository, plate_id)
    assert well(before, "A1")["concentration"] is None
    legacy_bundle = export_growth_tabular_data((GrowthRunView(before, (), ()),))
    legacy_rows = list(csv.DictReader(io.StringIO(legacy_bundle.measurements.content.decode())))
    legacy_a1 = next(
        row for row in legacy_rows if row["Well Row"] == "A" and row["Well Column"] == "1"
    )
    assert legacy_a1["Concentration"] == "2"
    legacy_meta = list(csv.DictReader(io.StringIO(legacy_bundle.metadata.content.decode())))
    assert next(row for row in legacy_meta if row["Well"] == "A1")["Treatment 2"] == "Drug B"
    assignment = CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "001")
    legacy_plan = prepare_condition_replicate_assignments(
        repository, before, registry, (assignment,)
    )[0]
    assert ["Drug A", "2e0", "mg/L"] in json.loads(str(legacy_plan.condition_key))["treatments"]

    with repository.transaction():
        repository.update_well_layout(plate_id, [{"position": "A1", "display_name": "Unrelated"}])
        repository.update_well_layout(plate_id, [{"position": "A1", "concentration": None}])
    unchanged = required_snapshot(repository, plate_id)
    assert "primary_condition_overrides" not in json_object(
        well(unchanged, "A1")["condition_custom_json"]
    )
    assert (
        prepare_condition_replicate_assignments(repository, unchanged, registry, (assignment,))[
            0
        ].condition_key
        == legacy_plan.condition_key
    )

    with repository.transaction():
        repository.update_well_layout(plate_id, [{"position": "A1", "concentration": 3}])
    structured = required_snapshot(repository, plate_id)
    structured_plan = prepare_condition_replicate_assignments(
        repository, structured, registry, (assignment,)
    )
    assert structured_plan[0].condition_key != legacy_plan.condition_key
    assert ["Drug A", "3e0", "mg/L"] in json.loads(str(structured_plan[0].condition_key))[
        "treatments"
    ]
    saved = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        plate_id,
        str(structured.metadata["updated_at"]),
        registry,
        structured_plan,
    )
    assert well(saved, "A1")["replicate"] == well(original, "A1")["replicate"]

    with repository.transaction():
        repository.update_well_layout(plate_id, [{"position": "A1", "concentration": None}])
    cleared = required_snapshot(repository, plate_id)
    condition_custom = json_object(well(cleared, "A1")["condition_custom_json"])
    assert "concentration" in condition_custom["primary_condition_overrides"]
    cleared_plan = prepare_condition_replicate_assignments(
        repository, cleared, registry, (assignment,)
    )
    assert cleared_plan[0].condition_key != structured_plan[0].condition_key
    assert ["Drug A", "", "mg/L"] in json.loads(str(cleared_plan[0].condition_key))["treatments"]
    with pytest.raises(ValueError, match="conditions changed"):
        export_growth_tabular_data((GrowthRunView(cleared, (), ()),))

    regenerated = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        plate_id,
        str(cleared.metadata["updated_at"]),
        registry,
        cleared_plan,
    )
    bundle = export_growth_tabular_data((GrowthRunView(regenerated, (), ()),))
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    a1 = next(row for row in rows if row["Well Row"] == "A" and row["Well Column"] == "1")
    assert a1["Concentration"] == ""
    assert a1["Condition 1 State"] == "Drug A mg/L"
    metadata_rows = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    metadata_a1 = next(row for row in metadata_rows if row["Well"] == "A1")
    assert metadata_a1["Treatment 2"] == "Drug B"
    assert metadata_a1["Concentration 2"] == "5"
    assert metadata_a1["Concentration unit 2"] == "mg/L"
    assert well(regenerated, "A1")["replicate"] == well(original, "A1")["replicate"]
    assert raw_hash(regenerated) == raw_before


def test_stale_condition_preview_rejects_save_without_partial_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    target = import_run(repository, unique=2, run_date=date(2026, 9, 12), medium="LB")
    target_before = required_snapshot(repository, target)
    registry = {"Team_Code": "PN", "CultivationReplicateMode": "condition"}
    proposed = prepare_condition_replicate_assignments(
        repository,
        target_before,
        registry,
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "002"),),
    )
    assert proposed[0].cultivation_replicate == 1
    import_run(repository, unique=1, run_date=date(2026, 8, 12), medium="LB")
    events_before = provenance_count(repository, target)
    with pytest.raises(DomainValidationError, match="refresh preview"):
        SaveGrowthCultivationsService(repository).execute(
            ACTOR,
            target,
            str(target_before.metadata["updated_at"]),
            registry,
            proposed,
        )
    target_after = required_snapshot(repository, target)
    assert target_after.metadata["plate_custom_json"] == target_before.metadata["plate_custom_json"]
    assert well(target_after, "A1")["custom_json"] == well(target_before, "A1")["custom_json"]
    assert provenance_count(repository, target) == events_before


def test_switching_to_local_mode_clears_condition_fields_only_on_assigned_well(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = import_run(repository, medium="LB")
    before = required_snapshot(repository, plate_id)
    condition_registry = {"Team_Code": "PN", "CultivationReplicateMode": "condition"}
    prepared = prepare_condition_replicate_assignments(
        repository,
        before,
        condition_registry,
        (
            CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "001"),
            CultivationAssignment("A2", "", DEFAULT_CULTIVATION_PATTERN, "001"),
        ),
    )
    service = SaveGrowthCultivationsService(repository)
    first = service.execute(
        ACTOR,
        plate_id,
        str(before.metadata["updated_at"]),
        condition_registry,
        prepared,
    )
    second_well_before = json_object(well(first, "A2")["custom_json"])
    local_registry = {
        "Team_Code": "PN",
        "CultivationSystemCode": "BRV",
        "CultivationReplicateMode": "local",
    }
    events_before = provenance_count(repository, plate_id)
    with pytest.raises(DomainValidationError, match="require condition mode"):
        service.execute(
            ACTOR,
            plate_id,
            str(first.metadata["updated_at"]),
            local_registry,
            (prepared[0],),
        )
    assert provenance_count(repository, plate_id) == events_before
    second = service.execute(
        ACTOR,
        plate_id,
        str(first.metadata["updated_at"]),
        local_registry,
        (CultivationAssignment("A1", "2"),),
    )
    first_well_after = json_object(well(second, "A1")["custom_json"])
    assert first_well_after["Cultivation"] == "PN-EXP-J3-BRV002R1"
    assert all(
        key not in first_well_after
        for key in (
            "LocalReplicate",
            "CultivationReplicate",
            "CultivationConditionKey",
            "CultivationReplicateScope",
            "CultivationConditionFields",
            "CultivationReplicateMode",
        )
    )
    assert json_object(well(second, "A2")["custom_json"]) == second_well_before
    assert well(second, "A1")["replicate"] == 1


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


def test_unsaved_suggestions_follow_experiment_dates_and_do_not_write(
    repository: SqlPlateReaderRepository,
) -> None:
    later_plate = import_run(repository, unique=2, run_date=date(2026, 9, 12))
    earlier_plate = import_run(repository, unique=1, run_date=date(2026, 8, 12))
    latest_plate = import_run(repository, unique=3, run_date=date(2026, 10, 12))
    ids = (earlier_plate, later_plate, latest_plate)
    snapshots = {plate_id: required_snapshot(repository, plate_id) for plate_id in ids}
    raw_hashes = {plate_id: raw_hash(snapshot) for plate_id, snapshot in snapshots.items()}
    event_counts = {plate_id: provenance_count(repository, plate_id) for plate_id in ids}

    assert suggested_cultivation_experiment_code(repository, latest_plate) == "003"
    assert suggested_cultivation_experiment_code(repository, earlier_plate) == "001"
    assert suggested_cultivation_experiment_code(repository, later_plate) == "002"
    assert suggested_cultivation_experiment_code(repository, earlier_plate) == "001"
    for plate_id in ids:
        assert raw_hash(required_snapshot(repository, plate_id)) == raw_hashes[plate_id]
        assert provenance_count(repository, plate_id) == event_counts[plate_id]

    saved_latest = SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        latest_plate,
        str(snapshots[latest_plate].metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationExperimentCode": "003"},
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "003"),),
    )
    assert (
        json_object(well(saved_latest, "A1")["custom_json"])["CultivationExperimentCode"] == "003"
    )
    assert suggested_cultivation_experiment_code(repository, earlier_plate) == "001"
    assert suggested_cultivation_experiment_code(repository, later_plate) == "002"

    experiment_id = ExperimentId(str(snapshots[earlier_plate].metadata["experiment_id"]))
    experiment_row = repository.connection.execute(
        "SELECT updated_at FROM experiments WHERE experiment_id = ?", (experiment_id,)
    ).fetchone()
    assert experiment_row is not None
    with repository.transaction():
        repository.update_experiment_metadata(
            experiment_id, str(experiment_row[0]), {"experiment_date": "2026-09-20"}
        )
    assert suggested_cultivation_experiment_code(repository, later_plate) == "001"
    assert suggested_cultivation_experiment_code(repository, earlier_plate) == "002"
    assert suggested_cultivation_experiment_code(repository, latest_plate) == "003"


def test_saving_newer_002_first_preserves_older_001_and_next_003(
    repository: SqlPlateReaderRepository,
) -> None:
    newer = import_run(repository, unique=2, run_date=date(2026, 9, 12))
    older = import_run(repository, unique=1, run_date=date(2026, 8, 12))
    next_plate = import_run(repository, unique=3, run_date=date(2026, 10, 12))
    assert suggested_cultivation_experiment_code(repository, older) == "001"
    assert suggested_cultivation_experiment_code(repository, newer) == "002"
    assert suggested_cultivation_experiment_code(repository, next_plate) == "003"

    before = required_snapshot(repository, newer)
    SaveGrowthCultivationsService(repository).execute(
        ACTOR,
        newer,
        str(before.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationExperimentCode": "002"},
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, "002"),),
    )
    assert suggested_cultivation_experiment_code(repository, older) == "001"
    assert suggested_cultivation_experiment_code(repository, newer) == "002"
    assert suggested_cultivation_experiment_code(repository, next_plate) == "003"


def test_sequential_suggestion_and_stale_number_conflict_are_transactional(
    repository: SqlPlateReaderRepository,
) -> None:
    first_plate_id = import_run(repository)
    second_plate_id = import_run(repository, unique=2)
    first_before = required_snapshot(repository, first_plate_id)
    second_before = required_snapshot(repository, second_plate_id)
    service = SaveGrowthCultivationsService(repository)
    stale_suggestion = suggested_cultivation_experiment_code(repository, first_plate_id)
    assert stale_suggestion == "001"
    assert suggested_cultivation_experiment_code(repository, second_plate_id) == "002"

    first = service.execute(
        ACTOR,
        first_plate_id,
        str(first_before.metadata["updated_at"]),
        {"Team_Code": "PN", "CultivationExperimentCode": stale_suggestion},
        (CultivationAssignment("A1", "", DEFAULT_CULTIVATION_PATTERN, stale_suggestion),),
    )
    assert json_object(well(first, "A1")["custom_json"])["CultivationExperimentCode"] == "001"
    assert suggested_cultivation_experiment_code(repository, second_plate_id) == "002"

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
    assert suggested_cultivation_experiment_code(repository, second_plate_id) == "002"

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
    assert suggested_cultivation_experiment_code(repository, first_plate_id) == "001"
    assert json_object(well(reused, "A1")["custom_json"])["CultivationExperimentCode"] == "001"

    with repository.transaction():
        repository.update_plate_metadata(
            first_plate_id,
            str(reused.metadata["updated_at"]),
            {"deleted_at": "2027-01-02T00:00:00", "deleted_by": str(ACTOR.user_id)},
        )
    assert suggested_cultivation_experiment_code(repository, second_plate_id) == "002"
    third_plate_id = import_run(repository, unique=3)
    third_before = required_snapshot(repository, third_plate_id)
    assert suggested_cultivation_experiment_code(repository, third_plate_id) == "003"
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
    repository: SqlPlateReaderRepository,
    *,
    duplicate_components: bool = False,
    unique: int = 1,
    run_date: date | None = None,
    medium: str | None = None,
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
            experiment_date=run_date or date(2026, 9, 12),
        ),
        CSV_TEXT,
        metadata=GrowthRunMetadata(plate_custom_json={"reader_meta": "kept"}),
        layout_changes=(
            WellLayoutChange(
                position="A1",
                strain="J3",
                medium=medium,
                replicate=1,
                custom_fields={"oxygen": "low"},
            ),
            WellLayoutChange(
                position="A2", strain=a2_strain, medium=medium, replicate=a2_replicate
            ),
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
