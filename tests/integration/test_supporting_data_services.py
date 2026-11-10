from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    DeleteOption,
    DeletePlateTemplate,
    ImportGrowthRun,
    Role,
    SaveOption,
    SavePlateTemplate,
    UpdateWellLayout,
    UserId,
)
from plate_reader.application.services import (
    DeleteLayoutColumnService,
    DeleteOptionService,
    DeletePlateTemplateService,
    ImportGrowthRunService,
    ListLayoutColumnsService,
    ListPlateTemplatesService,
    ListSavedOptionsService,
    SaveLayoutColumnService,
    SaveOptionService,
    SavePlateTemplateService,
    UpdateGrowthLayoutService,
)
from plate_reader.domain.common.plate import PLATE_96
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.database.repository import (
    ConcurrencyConflictError,
    InvalidRepositoryValueError,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.option_controls import saved_option_suggestions
from plate_reader.ui.plate_editor import (
    growth_layout_changes,
    growth_layout_frame_from_wells,
    include_layout_columns,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
ADMIN = Actor(UserId("template-admin"), "template-admin@example.invalid", Role.ADMIN)
EDITOR = Actor(UserId("template-editor"), "template-editor@example.invalid", Role.EDITOR)
VIEWER = Actor(UserId("template-viewer"), "template-viewer@example.invalid", Role.VIEWER)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"supporting-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    selected = SqlPlateReaderRepository(connection)
    with selected.transaction():
        for actor in (ADMIN, EDITOR, VIEWER):
            selected.upsert_user(
                {
                    "user_id": actor.user_id,
                    "email": actor.email,
                    "display_name": actor.email.split("@")[0],
                    "role": actor.role,
                    "is_active": True,
                }
            )
    try:
        yield selected
    finally:
        connection.close()


def test_template_create_update_list_delete_and_authorization(
    repository: SqlPlateReaderRepository,
) -> None:
    service = SavePlateTemplateService(repository, id_factory=lambda: "template-growth")
    created = service.execute(
        SavePlateTemplate(ADMIN, "Standard growth", AssayType.GROWTH, layout("strain-a"))
    )

    assert created.template_id == "template-growth"
    assert created.layout[0] == {"position": "A1", "strain": "strain-a"}
    assert ListPlateTemplatesService(repository).execute(VIEWER, AssayType.GROWTH) == (created,)
    assert ListPlateTemplatesService(repository).execute(VIEWER, AssayType.MIC) == ()

    updated = service.execute(
        SavePlateTemplate(
            ADMIN,
            "Standard growth revised",
            AssayType.GROWTH,
            layout("strain-b"),
            template_id=created.template_id,
            expected_updated_at=created.updated_at,
        )
    )
    assert updated.layout[0]["strain"] == "strain-b"
    with pytest.raises(ConcurrencyConflictError):
        service.execute(
            SavePlateTemplate(
                ADMIN,
                "Stale",
                AssayType.GROWTH,
                layout("strain-c"),
                template_id=created.template_id,
                expected_updated_at=created.updated_at,
            )
        )
    with pytest.raises(PermissionError):
        service.execute(SavePlateTemplate(EDITOR, "Forbidden", AssayType.GROWTH, layout("x")))
    with pytest.raises(InvalidRepositoryValueError, match="already exists"):
        SavePlateTemplateService(repository, id_factory=lambda: "template-other").execute(
            SavePlateTemplate(ADMIN, updated.template_name, AssayType.GROWTH, layout("x"))
        )

    DeletePlateTemplateService(repository).execute(
        DeletePlateTemplate(ADMIN, updated.template_id, updated.updated_at)
    )
    assert ListPlateTemplatesService(repository).execute(VIEWER) == ()


def test_saved_options_are_controlled_deduplicated_and_audited(
    repository: SqlPlateReaderRepository,
) -> None:
    saved = SaveOptionService(repository).execute(SaveOption(ADMIN, "medium", "M9"))
    duplicate = SaveOptionService(repository).execute(SaveOption(ADMIN, "MEDIUM", "m9"))

    assert saved.value == "M9"
    assert duplicate.value == "M9"
    assert ListSavedOptionsService(repository).execute(VIEWER, "medium") == (saved,)
    viewer_context = AppContext(repository, VIEWER)
    assert saved_option_suggestions(viewer_context, AssayType.GROWTH)["Media"] == ("M9",)
    assert saved_option_suggestions(viewer_context, AssayType.MIC)["Media"] == ("M9",)
    with pytest.raises(PermissionError):
        SaveOptionService(repository).execute(SaveOption(EDITOR, "medium", "LB"))

    DeleteOptionService(repository).execute(DeleteOption(ADMIN, "medium", "M9"))
    assert ListSavedOptionsService(repository).execute(VIEWER, "medium") == ()
    events = repository.connection.execute(
        "SELECT event_type FROM provenance_events ORDER BY occurred_at, event_id"
    ).fetchall()
    assert [row[0] for row in events].count("saved_option_added") == 1
    assert [row[0] for row in events].count("saved_option_deleted") == 1


def test_layout_columns_are_assay_wide_editor_managed_and_audited(
    repository: SqlPlateReaderRepository,
) -> None:
    saved = SaveLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, " Oxygen ")
    duplicate = SaveLayoutColumnService(repository).execute(ADMIN, AssayType.GROWTH, "oxygen")
    SaveLayoutColumnService(repository).execute(EDITOR, AssayType.MIC, "Oxygen")

    assert saved.name == "Oxygen"
    assert duplicate == saved
    assert tuple(
        column.name
        for column in ListLayoutColumnsService(repository).execute(VIEWER, AssayType.GROWTH)
    ) == ("Oxygen",)
    assert tuple(
        column.name
        for column in ListLayoutColumnsService(repository).execute(VIEWER, AssayType.MIC)
    ) == ("Oxygen",)
    with pytest.raises(ValueError, match="reserved"):
        SaveLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, "Run ID")
    for name in ("Treatment 2", "treatment_2", "Concentration 2", "conc_2", "unit_3"):
        with pytest.raises(ValueError, match="reserved"):
            SaveLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, name)
    with pytest.raises(PermissionError):
        SaveLayoutColumnService(repository).execute(VIEWER, AssayType.GROWTH, "Vessel")

    DeleteLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, "Oxygen")
    assert ListLayoutColumnsService(repository).execute(VIEWER, AssayType.GROWTH) == ()
    assert len(ListLayoutColumnsService(repository).execute(VIEWER, AssayType.MIC)) == 1
    events = repository.connection.execute(
        "SELECT event_type FROM provenance_events ORDER BY occurred_at, event_id"
    ).fetchall()
    assert [row[0] for row in events].count("layout_column_added") == 2
    assert [row[0] for row in events].count("layout_column_deleted") == 1


def test_template_validation_rejects_incomplete_or_nonfinite_layout(
    repository: SqlPlateReaderRepository,
) -> None:
    service = SavePlateTemplateService(repository)
    with pytest.raises(ValueError, match="each A1-H12"):
        service.execute(
            SavePlateTemplate(ADMIN, "Incomplete", AssayType.MIC, ({"position": "A1"},))
        )
    invalid = list(layout("strain-a"))
    invalid[0]["concentration"] = float("nan")
    with pytest.raises(ValueError, match="Out of range float values"):
        service.execute(SavePlateTemplate(ADMIN, "Nonfinite", AssayType.MIC, tuple(invalid)))


def layout(strain: str) -> tuple[dict[str, object], ...]:
    return tuple(
        {"position": position.label, "strain": strain if position.label == "A1" else ""}
        for position in PLATE_96.positions()
    )


def test_imported_and_registered_fields_are_available_across_growth_layouts(
    repository: SqlPlateReaderRepository,
) -> None:
    text = (ROOT / "tests/fixtures/growth/with_time.csv").read_text()
    plates = []
    for name in ("Checkerboard", "Single drug"):
        text += "\n"
        result = ImportGrowthRunService(repository).execute(
            ImportGrowthRun(
                EDITOR,
                name + ".csv",
                hashlib.sha256(text.encode()).hexdigest(),
                GROWTH_NORMALIZATION_VERSION,
                name,
                name,
                date(2026, 1, 2),
            ),
            text,
        )
        plates.append(result.plate_id)
    legacy = {"Drug 2": "B", "conc_2": 0, "Oxygen": "low", "CultivationConditionKey": "internal"}
    with repository.transaction():
        repository.update_well_layout(plates[0], [{"position": "A1", "custom_json": legacy}])
    SaveLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, "Unfilled")
    SaveLayoutColumnService(repository).execute(EDITOR, AssayType.MIC, "MIC only")
    catalog = ListLayoutColumnsService(repository)
    names = catalog.growth_editor_names(VIEWER)
    assert names == ("Oxygen", "Unfilled")
    snapshots = [repository.load_plate(plate) for plate in plates]
    first, second = [
        include_layout_columns(growth_layout_frame_from_wells(snap.wells), names)
        for snap in snapshots
    ]
    assert first.loc[0, ["Treatment 2", "Concentration 2", "Oxygen"]].tolist() == ["B", 0, "low"]
    assert second["Treatment 2"].isna().all() and second["Concentration 2"].isna().all()
    assert (second[["Oxygen", "Unfilled"]] == "").all().all()
    assert [repository.load_plate(plate) for plate in plates] == snapshots

    DeleteLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, "oxygen")
    assert catalog.growth_editor_names(VIEWER) == ("Unfilled",)
    assert json.loads(repository.load_plate(plates[0]).wells[0]["custom_json"]) == legacy
    SaveLayoutColumnService(repository).execute(EDITOR, AssayType.GROWTH, "OXYGEN")
    assert catalog.growth_editor_names(VIEWER) == ("OXYGEN", "Unfilled")


def test_growth_additional_treatments_persist_reload_and_clear_without_changing_raw_data(
    repository: SqlPlateReaderRepository,
) -> None:
    csv_text = (ROOT / "tests/fixtures/growth/with_time.csv").read_text(encoding="utf-8")
    imported = ImportGrowthRunService(repository).execute(
        ImportGrowthRun(
            EDITOR,
            "checkerboard.csv",
            hashlib.sha256(csv_text.encode()).hexdigest(),
            GROWTH_NORMALIZATION_VERSION,
            "Checkerboard",
            "Growth",
            date(2026, 1, 2),
        ),
        csv_text,
    )
    before = repository.load_plate(imported.plate_id)
    assert before is not None
    first_well_id = str(before.wells[0]["well_id"])
    condition_custom = {"Drug 2": "legacy second", "conc_2": "unknown", "unit_2": "uM"}
    identity = {"CultivationIdentity": {"number": "01", "history": ["00"]}}
    with repository.transaction():
        repository.connection.execute(
            "UPDATE well_conditions SET custom_json = ? WHERE well_id = ?",
            (json.dumps(condition_custom), first_well_id),
        )
        repository.update_well_layout(
            imported.plate_id,
            [{"position": "A1", "custom_json": identity}],
        )
    legacy = repository.load_plate(imported.plate_id)
    assert legacy is not None
    frame = growth_layout_frame_from_wells(legacy.wells)
    assert frame.loc[0, "Treatment 2"] == "legacy second"
    assert frame.loc[0, "Concentration 2"] == "unknown"
    frame.loc[0, "Treatment 3"] = "third agent"
    frame.loc[0, "Concentration 3"] = 0.0
    frame.loc[0, "Concentration unit 3"] = "mg/mL"
    saved = UpdateGrowthLayoutService(repository).execute(
        UpdateWellLayout(
            EDITOR,
            imported.plate_id,
            str(legacy.metadata["updated_at"]),
            growth_layout_changes(frame),
        ),
    )
    reloaded = growth_layout_frame_from_wells(saved.wells)
    assert reloaded.loc[0, "Treatment 2"] == "legacy second"
    assert reloaded.loc[0, "Treatment 3"] == "third agent"
    assert reloaded.loc[0, "Concentration 3"] == 0.0
    custom = json.loads(str(saved.wells[0]["custom_json"]))
    assert custom["CultivationIdentity"] == identity["CultivationIdentity"]
    assert saved.raw_observations == before.raw_observations
    for slot in (2, 3):
        for label in ("Treatment", "Concentration", "Concentration unit"):
            reloaded.loc[0, f"{label} {slot}"] = None
    cleared = UpdateGrowthLayoutService(repository).execute(
        UpdateWellLayout(
            EDITOR,
            imported.plate_id,
            str(saved.metadata["updated_at"]),
            growth_layout_changes(reloaded),
        ),
    )
    reopened = growth_layout_frame_from_wells(cleared.wells)
    assert reopened.loc[0, "Treatment 2"] is None
    assert reopened.loc[0, "Treatment 3"] is None
    assert cleared.raw_observations == before.raw_observations


def test_growth_saved_suggestions_are_shared_with_all_three_treatments(
    repository: SqlPlateReaderRepository,
) -> None:
    SaveOptionService(repository).execute(SaveOption(ADMIN, "treatment", "Drug A"))
    SaveOptionService(repository).execute(SaveOption(ADMIN, "concentration_unit", "uM"))
    suggestions = saved_option_suggestions(AppContext(repository, VIEWER), AssayType.GROWTH)
    for slot in (2, 3):
        assert suggestions[f"Treatment {slot}"] == suggestions["Treatment"] == ("Drug A",)
        assert suggestions[f"Concentration unit {slot}"] == ("uM",)
