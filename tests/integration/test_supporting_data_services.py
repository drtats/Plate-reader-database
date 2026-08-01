from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from plate_reader.application.contracts import (
    Actor,
    AssayType,
    DeleteOption,
    DeletePlateTemplate,
    Role,
    SaveOption,
    SavePlateTemplate,
    UserId,
)
from plate_reader.application.services import (
    DeleteOptionService,
    DeletePlateTemplateService,
    ListPlateTemplatesService,
    ListSavedOptionsService,
    SaveOptionService,
    SavePlateTemplateService,
)
from plate_reader.domain.common.plate import PLATE_96
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
