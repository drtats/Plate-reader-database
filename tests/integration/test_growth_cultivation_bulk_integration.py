from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from plate_reader.application.contracts import (
    Actor,
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
)
from plate_reader.application.services.growth_cultivation_bulk import (
    GrowthCultivationTarget,
    LoadBulkGrowthCultivationMetadataService,
    StaleGrowthCultivationMetadataError,
    UpdateBulkGrowthCultivationMetadataService,
)
from plate_reader.application.services.growth_import import ImportGrowthRunService
from plate_reader.application.services.growth_tabular_export import (
    ExportGrowthTabularData,
    ExportGrowthTabularDataService,
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
CSV_TEXT = (ROOT / "tests/fixtures/growth/with_time.csv").read_text(encoding="utf-8")
ACTOR = Actor(UserId("bulk-editor"), "bulk-editor@example.invalid", Role.EDITOR)


@pytest.fixture(params=tuple(DatabaseBackend), ids=lambda backend: backend.value)
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SqlPlateReaderRepository]:
    backend: DatabaseBackend = request.param
    connection = connect_database(
        DatabaseConfig(tmp_path / f"bulk-cultivation-{backend.value}.sqlite", backend, MIGRATIONS)
    )
    try:
        yield SqlPlateReaderRepository(connection)
    finally:
        connection.close()


def test_bulk_patch_persists_for_distinct_runs_without_raw_reads_and_flows_to_export(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    plate_ids = import_runs(repository)
    save_cultivation_ids(repository, plate_ids)
    before_ids = identity_rows(repository, plate_ids)
    raw_before = raw_series_rows(repository, plate_ids)
    wells_before = well_custom_rows(repository, plate_ids)
    loaded = LoadBulkGrowthCultivationMetadataService(repository).execute(ACTOR, plate_ids)
    original_load_plate = repository.load_plate

    def fail_raw_load(_plate_id: PlateId) -> None:
        raise AssertionError("bulk cultivation metadata must not load wells or raw observations")

    monkeypatch.setattr(repository, "load_plate", fail_raw_load)
    changed = UpdateBulkGrowthCultivationMetadataService(repository).execute(
        ACTOR,
        tuple(GrowthCultivationTarget(item.plate_id, item.updated_at) for item in loaded),
        {
            "Team_Code": "NEWTEAM",
            "CultivationSystemCode": "NEWSYS",
            "Objective": "bulk objective",
            "CultivationProtocol": "bulk protocol",
            "CultivationReplicateScope": "study-a",
            "CultivationConditionFields": "oxygen, shaking",
        },
        fill_missing_only=False,
    )
    monkeypatch.setattr(repository, "load_plate", original_load_plate)

    assert changed == plate_ids
    assert identity_rows(repository, plate_ids) == before_ids
    assert raw_series_rows(repository, plate_ids) == raw_before
    assert well_custom_rows(repository, plate_ids) == wells_before
    reloaded = LoadBulkGrowthCultivationMetadataService(repository).execute(ACTOR, plate_ids)
    assert all(item.registry["Objective"] == "bulk objective" for item in reloaded)
    assert all(item.registry["CultivationProtocol"] == "bulk protocol" for item in reloaded)
    assert all(item.registry["Team_Code"] == "NEWTEAM" for item in reloaded)
    assert all(item.registry["CultivationSystemCode"] == "NEWSYS" for item in reloaded)
    assert all(item.registry["CultivationReplicateScope"] == "study-a" for item in reloaded)
    assert all(
        item.registry["CultivationConditionFields"] == "oxygen, shaking" for item in reloaded
    )
    for plate_id in plate_ids:
        custom = json.loads(
            cast(
                str,
                required_row(
                    repository.connection.execute(
                        "SELECT custom_json FROM plates WHERE plate_id = ?", (plate_id,)
                    ).fetchone()
                )[0],
            )
        )
        assert custom["reader_meta"] in {"marker-0", "marker-1"}

    bundle = ExportGrowthTabularDataService(repository).execute(
        ExportGrowthTabularData(ACTOR, plate_ids)
    )
    rows = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode("utf-8"))))
    first_a1 = next(row for row in rows if row["Run ID"] == plate_ids[0] and row["Well"] == "A1")
    first_a2 = next(row for row in rows if row["Run ID"] == plate_ids[0] and row["Well"] == "A2")
    second_a1 = next(row for row in rows if row["Run ID"] == plate_ids[1] and row["Well"] == "A1")
    assert first_a1["Objective"] == "well override"
    assert first_a2["Objective"] == "bulk objective"
    assert second_a1["Objective"] == "bulk objective"
    assert {row["CultivationProtocol"] for row in rows} == {"bulk protocol"}

    events = repository.connection.execute(
        "SELECT entity_id, details_json FROM provenance_events "
        "WHERE event_type = 'cultivation_metadata_updated' ORDER BY entity_id"
    ).fetchall()
    bulk_events = [row for row in events if "bulk" in json.loads(row[1])]
    assert [row[0] for row in bulk_events] == sorted(plate_ids)
    assert all(json.loads(row[1])["bulk"]["target_count"] == 2 for row in bulk_events)


def test_missing_or_deleted_growth_selection_and_provenance_failure_are_atomic(
    repository: SqlPlateReaderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    plate_ids = import_runs(repository)
    loaded = LoadBulkGrowthCultivationMetadataService(repository).execute(ACTOR, plate_ids)
    repository.connection.execute(
        "UPDATE plates SET deleted_at = '2026-09-12T00:00:00Z', deleted_by = ? WHERE plate_id = ?",
        (ACTOR.user_id, plate_ids[1]),
    )
    repository.connection.commit()

    with pytest.raises(LookupError, match=str(plate_ids[1])):
        UpdateBulkGrowthCultivationMetadataService(repository).execute(
            ACTOR,
            tuple(GrowthCultivationTarget(item.plate_id, item.updated_at) for item in loaded),
            {"Comment": "must roll back"},
            fill_missing_only=False,
        )
    assert registry(repository, plate_ids[0])["Comment"] == "old plate-0"

    repository.connection.execute(
        "UPDATE plates SET deleted_at = NULL, deleted_by = NULL WHERE plate_id = ?",
        (plate_ids[1],),
    )
    repository.connection.commit()
    refreshed = LoadBulkGrowthCultivationMetadataService(repository).execute(ACTOR, plate_ids)

    repository.connection.execute(
        "UPDATE plates SET assay_type = 'mic' WHERE plate_id = ?", (plate_ids[1],)
    )
    repository.connection.commit()
    with pytest.raises(LookupError, match=str(plate_ids[1])):
        LoadBulkGrowthCultivationMetadataService(repository).execute(ACTOR, plate_ids)
    repository.connection.execute(
        "UPDATE plates SET assay_type = 'growth' WHERE plate_id = ?", (plate_ids[1],)
    )
    repository.connection.commit()

    with pytest.raises(StaleGrowthCultivationMetadataError):
        UpdateBulkGrowthCultivationMetadataService(repository).execute(
            ACTOR,
            (
                GrowthCultivationTarget(refreshed[0].plate_id, "stale version"),
                GrowthCultivationTarget(refreshed[1].plate_id, refreshed[1].updated_at),
            ),
            {"Comment": "must roll back"},
            fill_missing_only=False,
        )
    assert registry(repository, plate_ids[0])["Comment"] == "old plate-0"

    def fail_provenance(_values: object) -> str:
        raise RuntimeError("forced provenance failure")

    monkeypatch.setattr(repository, "append_provenance", fail_provenance)
    with pytest.raises(RuntimeError, match="forced provenance failure"):
        UpdateBulkGrowthCultivationMetadataService(repository).execute(
            ACTOR,
            tuple(GrowthCultivationTarget(item.plate_id, item.updated_at) for item in refreshed),
            {"Comment": "must roll back"},
            fill_missing_only=False,
        )
    assert registry(repository, plate_ids[0])["Comment"] == "old plate-0"
    assert registry(repository, plate_ids[1])["Comment"] == "old plate-1"


def import_runs(repository: SqlPlateReaderRepository) -> tuple[PlateId, PlateId]:
    identifiers = itertools.count()
    service = ImportGrowthRunService(
        repository, id_factory=lambda: f"bulk-cultivation-{next(identifiers):04d}"
    )
    plate_ids: list[PlateId] = []
    for index in range(2):
        result = service.execute(
            ImportGrowthRun(
                actor=ACTOR,
                source_name=f"bulk-{index}.csv",
                source_sha256=hashlib.sha256(CSV_TEXT.encode()).hexdigest(),
                parser_version=GROWTH_NORMALIZATION_VERSION,
                experiment_name=f"Bulk experiment {index}",
                plate_name=f"Bulk plate {index}",
                experiment_date=date(2026, 9, 12),
                idempotency_key=f"bulk-cultivation-{index}",
            ),
            CSV_TEXT,
            metadata=GrowthRunMetadata(
                plate_custom_json={
                    "reader_meta": f"marker-{index}",
                    "cultivation_registry": {
                        "Team_Code": "PN",
                        "Comment": f"old plate-{index}",
                    },
                }
            ),
            layout_changes=(
                WellLayoutChange(
                    position="A1",
                    strain=f"J{index + 3}",
                    replicate=1,
                    custom_fields={"Objective": "well override"} if index == 0 else {},
                ),
            ),
        )
        plate_ids.append(result.plate_id)
    return plate_ids[0], plate_ids[1]


def save_cultivation_ids(
    repository: SqlPlateReaderRepository, plate_ids: tuple[PlateId, PlateId]
) -> None:
    service = SaveGrowthCultivationsService(repository)
    for index, plate_id in enumerate(plate_ids):
        snapshot = repository.load_plate(plate_id)
        assert snapshot is not None
        service.execute(
            ACTOR,
            plate_id,
            str(snapshot.metadata["updated_at"]),
            {
                "Team_Code": "PN",
                "CultivationSystemCode": "BRV",
                "Comment": f"old plate-{index}",
            },
            (CultivationAssignment("A1", str(index + 1)),),
        )


def identity_rows(
    repository: SqlPlateReaderRepository, plate_ids: tuple[PlateId, PlateId]
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        required_row(
            repository.connection.execute(
                "SELECT plate_id, experiment_id, assay_type FROM plates WHERE plate_id = ?",
                (plate_id,),
            ).fetchone()
        )
        for plate_id in plate_ids
    )


def raw_series_rows(
    repository: SqlPlateReaderRepository, plate_ids: tuple[PlateId, PlateId]
) -> tuple[tuple[object, ...], ...]:
    placeholders = ", ".join("?" for _ in plate_ids)
    return tuple(
        required_row(row)
        for row in repository.connection.execute(
            "SELECT plate_id, channel, positions_json, timepoints_blob, values_blob, "
            "content_sha256 FROM growth_series_chunks "
            f"WHERE plate_id IN ({placeholders}) ORDER BY plate_id, channel",
            plate_ids,
        ).fetchall()
    )


def well_custom_rows(
    repository: SqlPlateReaderRepository, plate_ids: tuple[PlateId, PlateId]
) -> tuple[tuple[object, ...], ...]:
    placeholders = ", ".join("?" for _ in plate_ids)
    rows = tuple(
        required_row(row)
        for row in repository.connection.execute(
            "SELECT plate_id, position, custom_json FROM wells "
            f"WHERE plate_id IN ({placeholders}) ORDER BY plate_id, position",
            plate_ids,
        ).fetchall()
    )
    a1_custom = [json.loads(cast(str, row[2])) for row in rows if row[1] == "A1"]
    assert all(custom.get("Cultivation") for custom in a1_custom)
    return rows


def registry(repository: SqlPlateReaderRepository, plate_id: PlateId) -> dict[str, object]:
    value = cast(
        str,
        required_row(
            repository.connection.execute(
                "SELECT custom_json FROM plates WHERE plate_id = ?", (plate_id,)
            ).fetchone()
        )[0],
    )
    parsed = json.loads(value)
    return cast(dict[str, object], parsed["cultivation_registry"])


def required_row(value: tuple[object, ...] | None) -> tuple[object, ...]:
    assert value is not None
    return value
