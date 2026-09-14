"""Real-database checks for metadata-only plate/condition ID persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import Actor, ImportGrowthRun, PlateId, Role, UserId
from plate_reader.application.services.authorization import AuthorizationError
from plate_reader.application.services.growth_cultivation_registry import (
    PreviewGrowthCultivationRegistryService,
    SaveGrowthCultivationRegistryService,
    StaleGrowthCultivationRegistryError,
)
from plate_reader.application.services.growth_import import ImportGrowthRunService
from plate_reader.domain.common import DomainValidationError
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.domain.growth.cultivation_registry import RegistrySettings
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)

ROOT = Path(__file__).resolve().parents[2]
GROWTH_CSV = (ROOT / "tests/fixtures/growth/with_time.csv").read_text(encoding="utf-8")
LABEL_CSV = (ROOT / "tests/fixtures/growth/labels.csv").read_text(encoding="utf-8")
EDITOR = Actor(UserId("editor-id"), "editor@example.invalid", Role.EDITOR)
VIEWER = Actor(UserId("editor-id"), "editor@example.invalid", Role.VIEWER)
SETTINGS = RegistrySettings(team_code="ST", system_code="MP96A")


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"registry-{backend.value}.sqlite", backend, ROOT / "migrations")
    )
    try:
        yield SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def _import_plate(repository: SqlPlateReaderRepository, name: str = "Plate 1") -> PlateId:
    csv_text = GROWTH_CSV if name == "Plate 1" else GROWTH_CSV + "\n"
    result = ImportGrowthRunService(repository).execute(
        ImportGrowthRun(
            actor=EDITOR,
            source_name=f"{name}.csv",
            source_sha256=hashlib.sha256(csv_text.encode()).hexdigest(),
            parser_version=GROWTH_NORMALIZATION_VERSION,
            experiment_name=name,
            plate_name=name,
            experiment_date=date(2026, 1, 2),
        ),
        csv_text,
        label_csv_text=LABEL_CSV,
    )
    with repository.transaction():
        repository.update_well_layout(
            result.plate_id,
            [
                {
                    "position": position,
                    "strain": "MG1655",
                    "medium": "M9",
                    "treatment": "drug",
                    "concentration": 1.0,
                    "concentration_unit": "uM",
                }
                for position in ("A1", "A2")
            ],
        )
    return result.plate_id


def _well_custom(repository: SqlPlateReaderRepository, plate_id: PlateId, position: str) -> dict:
    row = repository.connection.execute(
        "SELECT custom_json FROM wells WHERE plate_id = ? AND position = ?", (plate_id, position)
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


def _counts(repository: SqlPlateReaderRepository) -> tuple[int, int]:
    return tuple(
        int(repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in ("growth_measurements", "analysis_revisions")
    )  # type: ignore[return-value]


def _registry_state(repository: SqlPlateReaderRepository, plate_id: PlateId) -> tuple:
    """Capture persisted registry state without loading measurements."""
    return (
        repository.growth_cultivation_metadata((plate_id,)),
        _well_custom(repository, plate_id, "A1"),
        repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0],
        _counts(repository),
    )


@pytest.mark.parametrize(
    "plate_ids, message",
    [
        ((), "Select between 1 and 500 Growth plates"),
        (("",), "Growth plate IDs must be non-empty strings"),
        (("missing", "missing"), "Growth plate IDs must be unique"),
        (("missing",) * 501, "Select between 1 and 500 Growth plates"),
    ],
)
def test_preview_rejects_invalid_selection_without_writes(
    repository: SqlPlateReaderRepository, plate_ids: tuple[str, ...], message: str
) -> None:
    plate_id = _import_plate(repository)
    before = _registry_state(repository, plate_id)
    with pytest.raises(DomainValidationError, match=message):
        PreviewGrowthCultivationRegistryService(repository).execute(EDITOR, plate_ids, SETTINGS)
    assert _registry_state(repository, plate_id) == before


def test_preview_rejects_invalid_settings_and_missing_plate_without_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    before = _registry_state(repository, plate_id)
    with pytest.raises(DomainValidationError, match="Invalid cultivation registry settings"):
        PreviewGrowthCultivationRegistryService(repository).execute(
            EDITOR,
            (plate_id,),
            "invalid settings",  # type: ignore[arg-type]
        )
    with pytest.raises(LookupError, match="Active Growth plates not found: missing"):
        PreviewGrowthCultivationRegistryService(repository).execute(
            EDITOR, (plate_id, PlateId("missing")), SETTINGS
        )
    assert _registry_state(repository, plate_id) == before


def test_save_rejects_deleted_plate_after_preview_without_partial_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    current = repository.growth_cultivation_metadata((plate_id,))[0]
    with repository.transaction():
        repository.update_plate_metadata(
            plate_id,
            str(current["updated_at"]),
            {"deleted_at": "2026-09-14T00:00:00Z", "deleted_by": str(EDITOR.user_id)},
        )
    before = (
        _well_custom(repository, plate_id, "A1"),
        repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0],
        _counts(repository),
    )
    with pytest.raises(LookupError, match="Active Growth plates not found"):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert (
        _well_custom(repository, plate_id, "A1"),
        repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0],
        _counts(repository),
    ) == before


def test_save_rejects_invalid_preview_without_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    before = _registry_state(repository, plate_id)
    with pytest.raises(DomainValidationError, match="Save requires a cultivation registry preview"):
        SaveGrowthCultivationRegistryService(repository).execute(
            EDITOR,
            object(),  # type: ignore[arg-type]
        )
    assert _registry_state(repository, plate_id) == before


def test_duplicate_legacy_ids_elsewhere_in_library_block_save_without_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    other_id = _import_plate(repository, "Plate 2")
    with repository.transaction():
        repository.update_well_layout(
            other_id,
            [
                {"position": position, "custom_json": {"Cultivation": "LEGACY-DUPLICATE"}}
                for position in ("B1", "B2")
            ],
        )
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    before = _registry_state(repository, plate_id)
    with pytest.raises(DomainValidationError, match="Duplicate saved cultivation ID in library"):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert _registry_state(repository, plate_id) == before


def test_conflicting_legacy_id_on_another_plate_blocks_preview_without_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    other_id = _import_plate(repository, "Plate 2")
    initial = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    proposed_id = next(
        assignment["Cultivation"]
        for assignment in initial.plates[0].assignments
        if assignment["Well"] == "A1"
    )
    with repository.transaction():
        repository.update_well_layout(
            other_id,
            [{"position": "B1", "custom_json": {"Cultivation": proposed_id}}],
        )
    before = _registry_state(repository, plate_id)
    with pytest.raises(DomainValidationError, match="collides with another saved ID"):
        PreviewGrowthCultivationRegistryService(repository).execute(EDITOR, (plate_id,), SETTINGS)
    assert _registry_state(repository, plate_id) == before


def test_inactive_stored_user_cannot_preview_or_save(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": EDITOR.user_id,
                "email": EDITOR.email,
                "display_name": "Inactive Editor",
                "role": "editor",
                "is_active": False,
            }
        )
    before = _registry_state(repository, plate_id)
    with pytest.raises(AuthorizationError, match="inactive"):
        PreviewGrowthCultivationRegistryService(repository).execute(EDITOR, (plate_id,), SETTINGS)
    with pytest.raises(AuthorizationError, match="inactive"):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert _registry_state(repository, plate_id) == before


def test_preview_is_metadata_only_and_save_preserves_raw_and_all_internal_ids(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    plate_id = _import_plate(repository)
    raw_counts = _counts(repository)

    def forbidden_load(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("raw plate snapshot was loaded")

    monkeypatch.setattr(repository, "load_plate", forbidden_load)
    monkeypatch.setattr(repository, "stream_growth_measurements", forbidden_load)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        VIEWER, (plate_id,), SETTINGS
    )
    assert preview.plates[0].plate_number == "01"
    assert not _well_custom(repository, plate_id, "A1").get("Cultivation")

    changed = SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert changed == (plate_id,)
    a1 = _well_custom(repository, plate_id, "A1")
    a2 = _well_custom(repository, plate_id, "A2")
    assert a1["Cultivation"].startswith("ST-EXP-MG1655-MP96A0101R")
    assert a2["Cultivation"].startswith("ST-EXP-MG1655-MP96A0101R")
    assert a1["Cultivation"] != a2["Cultivation"]
    assert a1["InternalCultivationID"] != a2["InternalCultivationID"]
    assert a1["Local_Cultivation_ID"] == "EXP01-A01"
    assert _well_custom(repository, plate_id, "B1")["InternalCultivationID"]
    assert _counts(repository) == raw_counts
    assert _well_custom(repository, plate_id, "B1").get("Cultivation") in (None, "")

    provenance_count = repository.connection.execute(
        "SELECT count(*) FROM provenance_events"
    ).fetchone()[0]
    new_preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    assert SaveGrowthCultivationRegistryService(repository).execute(EDITOR, new_preview) == ()
    assert (
        repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0]
        == provenance_count
    )


def test_save_rejects_stale_condition_and_viewer_without_writes(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    with pytest.raises(AuthorizationError):
        SaveGrowthCultivationRegistryService(repository).execute(VIEWER, preview)
    with repository.transaction():
        repository.update_well_layout(plate_id, [{"position": "A2", "medium": "LB"}])
    before = repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0]
    with pytest.raises(StaleGrowthCultivationRegistryError):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert _well_custom(repository, plate_id, "A1").get("Cultivation") is None
    assert (
        repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0]
        == before
    )


def test_stored_viewer_role_blocks_editor_actor(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": EDITOR.user_id,
                "email": EDITOR.email,
                "display_name": "Stored Viewer",
                "role": "viewer",
                "is_active": True,
            }
        )
    with pytest.raises(AuthorizationError):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert _well_custom(repository, plate_id, "A1").get("Cultivation") is None


def test_mid_save_failure_rolls_back_plate_and_wells(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    plate_id = _import_plate(repository)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    before = repository.growth_cultivation_metadata((plate_id,))[0]
    provenance_count = repository.connection.execute(
        "SELECT count(*) FROM provenance_events"
    ).fetchone()[0]

    def fail_after_plate(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("well update failed")

    monkeypatch.setattr(repository, "update_well_layout", fail_after_plate)
    with pytest.raises(RuntimeError, match="well update failed"):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    after = repository.growth_cultivation_metadata((plate_id,))[0]
    assert after["updated_at"] == before["updated_at"]
    assert after["plate_custom_json"] == before["plate_custom_json"]
    assert _well_custom(repository, plate_id, "A1").get("Cultivation") is None
    assert (
        repository.connection.execute("SELECT count(*) FROM provenance_events").fetchone()[0]
        == provenance_count
    )


def test_legacy_id_history_and_unrelated_metadata_survive_save(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    current = repository.growth_cultivation_metadata((plate_id,))[0]
    with repository.transaction():
        repository.update_plate_metadata(
            plate_id,
            str(current["updated_at"]),
            {
                "custom_json": {
                    "lid": "clear",
                    "cultivation_registry": {"Comment": "keep this description"},
                }
            },
        )
        repository.update_well_layout(
            plate_id,
            [
                {
                    "position": "A1",
                    "custom_json": {
                        "Cultivation": "LEGACY-A1",
                        "PreviousCultivationIDs": ["OLDER-A1"],
                        "well_note": "keep this note",
                    },
                }
            ],
        )
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    assert SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview) == (plate_id,)
    custom = _well_custom(repository, plate_id, "A1")
    assert custom["PreviousCultivationIDs"] == ["OLDER-A1", "LEGACY-A1"]
    assert custom["well_note"] == "keep this note"
    plate_custom = json.loads(
        repository.growth_cultivation_metadata((plate_id,))[0]["plate_custom_json"]
    )
    assert plate_custom["lid"] == "clear"
    assert plate_custom["cultivation_registry"]["Comment"] == "keep this description"
    event = repository.connection.execute(
        "SELECT details_json FROM provenance_events WHERE entity_id = ? "
        "AND event_type = 'cultivation_registry_saved'",
        (plate_id,),
    ).fetchone()
    assert event is not None
    assert "LEGACY-A1" in event[0]


def test_new_global_plate_reservation_invalidates_older_preview(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    assert preview.plates[0].plate_number == "01"
    second_id = _import_plate(repository, "Plate 2")
    second_metadata = repository.growth_cultivation_metadata((second_id,))[0]
    with repository.transaction():
        repository.update_plate_metadata(
            second_id,
            str(second_metadata["updated_at"]),
            {
                "custom_json": {
                    "cultivation_registry": {
                        "scheme": "plate_condition_v1",
                        "CultivationPlateNumber": "01",
                    }
                }
            },
        )
    with pytest.raises(StaleGrowthCultivationRegistryError):
        SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    assert _well_custom(repository, plate_id, "A1").get("Cultivation") is None


def test_legacy_id_on_missing_strain_moves_to_history(
    repository: SqlPlateReaderRepository,
) -> None:
    plate_id = _import_plate(repository)
    before_row = next(
        row
        for row in repository.growth_cultivation_wells()
        if row["plate_id"] == plate_id and row["position"] == "B1"
    )
    with repository.transaction():
        repository.update_well_layout(
            plate_id,
            [
                {
                    "position": "B1",
                    "custom_json": {"Cultivation": "LEGACY-B1", "note": "keep"},
                }
            ],
        )
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    custom = _well_custom(repository, plate_id, "B1")
    assert custom["Cultivation"] == ""
    assert custom["PreviousCultivationIDs"] == ["LEGACY-B1"]
    assert custom["InternalCultivationID"] == before_row["well_id"]
    assert custom["note"] == "keep"


def test_normalized_strain_ids_save_and_export_original_metadata(
    repository: SqlPlateReaderRepository,
) -> None:
    import csv
    import io

    from plate_reader.application.services.growth_tabular_export import (
        ExportGrowthTabularData,
        ExportGrowthTabularDataService,
    )

    plate_id = _import_plate(repository)
    original = "ΔacrB MG 1-2"
    with repository.transaction():
        repository.update_well_layout(
            plate_id,
            [{"position": position, "strain": original} for position in ("A1", "A2")],
        )
    before = repository.load_plate(plate_id)
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    preview_again = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    assert SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview_again) == ()
    saved = repository.load_plate(plate_id)
    assert saved.wells[0]["strain"] == before.wells[0]["strain"]
    bundle = ExportGrowthTabularDataService(repository).execute(
        ExportGrowthTabularData(EDITOR, (plate_id,))
    )
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    data = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    for position, replicate in (("A1", 1), ("A2", 2)):
        row = next(row for row in metadata if row["Well"] == position)
        assert row["Strain"] == original
        assert row["Cultivation"] == f"ST-EXP-dacrB_MG_1_2-MP96A0101R{replicate}"
        assert row["CultivationExperiment"] == "ST-EXP-dacrB_MG_1_2-MP96A[0101]"
        observations = [row for row in data if row["Well"] == position]
        assert observations
        assert all(item["Strain"] == original for item in observations)
        assert all(item["Cultivation ID"] == row["Cultivation"] for item in observations)
    assert repr(repository.load_plate(plate_id)) == repr(saved)


def test_saved_export_requires_commit_and_detects_new_unassigned_wells(
    repository: SqlPlateReaderRepository,
) -> None:
    import csv
    import io

    from plate_reader.application.services.growth_tabular_export import (
        ExportGrowthTabularData,
        ExportGrowthTabularDataService,
    )

    plate_id = _import_plate(repository, "Cultivation experiment")
    preview = PreviewGrowthCultivationRegistryService(repository).execute(
        EDITOR, (plate_id,), SETTINGS
    )
    command = ExportGrowthTabularData(EDITOR, (plate_id,), require_saved_cultivation_ids=True)
    exporter = ExportGrowthTabularDataService(repository)
    before = repr(repository.load_plate(plate_id))
    with pytest.raises(ValueError, match="Cultivation experiment"):
        exporter.execute(command)
    assert repr(repository.load_plate(plate_id)) == before
    SaveGrowthCultivationRegistryService(repository).execute(EDITOR, preview)
    saved = repr(repository.load_plate(plate_id))
    bundle = exporter.execute(command)
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    for well in ("A1", "A2"):
        registered = next(row for row in metadata if row["Well"] == well)
        assert registered["Cultivation"]
        assert all(
            row["Cultivation ID"] == registered["Cultivation"]
            for row in rows
            if row["Well"] == well
        )
    assert repr(repository.load_plate(plate_id)) == saved
    # A formerly unspecified well becomes an eligible culture: save its assignment before export.
    with repository.transaction():
        repository.update_well_layout(
            plate_id, [{"position": "B1", "strain": "MG1655", "is_blank": 0}]
        )
    with pytest.raises(ValueError, match="not been saved"):
        exporter.execute(command)
