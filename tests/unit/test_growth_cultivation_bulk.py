from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import cast

import pytest

from plate_reader.application.contracts import Actor, PlateId, Role, UserId
from plate_reader.application.services.growth_cultivation_bulk import (
    CULTIVATION_SHARED_FIELDS,
    GrowthCultivationTarget,
    LoadBulkGrowthCultivationMetadataService,
    StaleGrowthCultivationMetadataError,
    UpdateBulkGrowthCultivationMetadataService,
)
from plate_reader.domain.common import DomainValidationError, IssueCode

EDITOR = Actor(UserId("editor-id"), "editor@example.invalid", Role.EDITOR)
VIEWER = Actor(UserId("viewer-id"), "viewer@example.invalid", Role.VIEWER)


class FakeRepository:
    def __init__(self) -> None:
        self.users = {
            EDITOR.email: {
                "user_id": EDITOR.user_id,
                "role": EDITOR.role,
                "is_active": True,
            },
            VIEWER.email: {
                "user_id": VIEWER.user_id,
                "role": VIEWER.role,
                "is_active": True,
            },
        }
        self.rows = {
            PlateId("plate-1"): row(
                "plate-1",
                "v1",
                {
                    "reader_meta": {"gain": 3},
                    "cultivation_registry": {
                        "Team_Code": "PN",
                        "Objective": "existing",
                        "Comment": "  ",
                    },
                },
            ),
            PlateId("plate-2"): row(
                "plate-2",
                "v2",
                {"unrelated": True, "cultivation_registry": {"Team_Code": None}},
            ),
        }
        self.events: list[Mapping[str, object]] = []
        self.reads = 0
        self.fail_provenance = False
        self.late_conflict_for: PlateId | None = None

    def user_by_email(self, email: str) -> Mapping[str, object] | None:
        return self.users.get(email)

    def growth_cultivation_metadata(
        self, plate_ids: Sequence[PlateId]
    ) -> tuple[dict[str, object], ...]:
        self.reads += 1
        return tuple(copy.deepcopy(self.rows[item]) for item in plate_ids if item in self.rows)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        before = copy.deepcopy((self.rows, self.events))
        try:
            yield
        except Exception:
            self.rows, self.events = before
            raise

    def update_plate_metadata(
        self, plate_id: PlateId, expected_updated_at: str, changes: dict[str, object]
    ) -> str:
        if self.late_conflict_for == plate_id:
            self.rows[plate_id]["updated_at"] = "concurrent"
        if self.rows[plate_id]["updated_at"] != expected_updated_at:
            raise RuntimeError("changed since it was loaded")
        self.rows[plate_id]["plate_custom_json"] = copy.deepcopy(changes["custom_json"])
        self.rows[plate_id]["updated_at"] = f"next-{plate_id}"
        return str(self.rows[plate_id]["updated_at"])

    def append_provenance(self, values: Mapping[str, object]) -> str:
        if self.fail_provenance:
            raise RuntimeError("forced provenance failure")
        self.events.append(copy.deepcopy(values))
        return f"event-{len(self.events)}"

    def load_plate(self, _plate_id: PlateId) -> None:
        raise AssertionError("bulk cultivation service must not load a plate")


def test_load_requires_editor_and_preserves_requested_order() -> None:
    repository = FakeRepository()
    loaded = LoadBulkGrowthCultivationMetadataService(repository).execute(
        EDITOR, (PlateId("plate-2"), PlateId("plate-1"))
    )

    assert [item.plate_id for item in loaded] == ["plate-2", "plate-1"]
    assert loaded[1].experiment_name == "Experiment plate-1"
    assert loaded[1].registry["Objective"] == "existing"
    loaded[1].registry["Objective"] = "caller mutation"
    assert repository.rows[PlateId("plate-1")]["plate_custom_json"] != loaded[1].registry

    with pytest.raises(PermissionError):
        LoadBulkGrowthCultivationMetadataService(repository).execute(VIEWER, (PlateId("plate-1"),))
    assert repository.reads == 1


def test_fill_missing_updates_only_blank_fields_and_returns_changed_ids() -> None:
    repository = FakeRepository()
    changed = UpdateBulkGrowthCultivationMetadataService(repository).execute(
        EDITOR,
        targets(repository),
        {"Team_Code": "TEAM", "Objective": "new", "Comment": "filled"},
    )

    assert changed == (PlateId("plate-1"), PlateId("plate-2"))
    first_custom = custom(repository, "plate-1")
    second_custom = custom(repository, "plate-2")
    assert first_custom["reader_meta"] == {"gain": 3}
    assert first_custom["cultivation_registry"] == {
        "Team_Code": "PN",
        "Objective": "existing",
        "Comment": "filled",
    }
    assert second_custom["unrelated"] is True
    assert second_custom["cultivation_registry"] == {
        "Team_Code": "TEAM",
        "Objective": "new",
        "Comment": "filled",
    }
    assert [event["entity_id"] for event in repository.events] == ["plate-1", "plate-2"]
    details = repository.events[0]["details_json"]
    assert isinstance(details, Mapping)
    assert details["bulk"] == {
        "fill_missing_only": True,
        "fields": ["Team_Code", "Objective", "Comment"],
        "target_count": 2,
    }


def test_replace_supports_selective_clear_and_noop_returns_empty() -> None:
    repository = FakeRepository()
    service = UpdateBulkGrowthCultivationMetadataService(repository)
    changed = service.execute(
        EDITOR,
        (GrowthCultivationTarget(PlateId("plate-1"), "v1"),),
        {"Objective": "", "Team_Code": "PN"},
        fill_missing_only=False,
    )
    assert changed == (PlateId("plate-1"),)
    assert custom(repository, "plate-1")["cultivation_registry"] == {
        "Team_Code": "PN",
        "Objective": "",
        "Comment": "  ",
    }

    assert (
        service.execute(
            EDITOR,
            (GrowthCultivationTarget(PlateId("plate-1"), "next-plate-1"),),
            {"Team_Code": "PN"},
        )
        == ()
    )
    assert len(repository.events) == 1


def test_missing_and_stale_noop_targets_reject_entire_batch() -> None:
    repository = FakeRepository()
    service = UpdateBulkGrowthCultivationMetadataService(repository)
    with pytest.raises(LookupError, match="missing"):
        service.execute(
            EDITOR,
            (
                GrowthCultivationTarget(PlateId("plate-1"), "v1"),
                GrowthCultivationTarget(PlateId("missing"), "v1"),
            ),
            {"Comment": "value"},
        )
    assert registry(repository, "plate-1")["Comment"] == "  "

    with pytest.raises(StaleGrowthCultivationMetadataError) as raised:
        service.execute(
            EDITOR,
            (
                GrowthCultivationTarget(PlateId("plate-1"), "stale"),
                GrowthCultivationTarget(PlateId("plate-2"), "v2"),
            ),
            {"Team_Code": "TEAM"},
        )
    assert raised.value.plate_ids == (PlateId("plate-1"),)
    assert repository.events == []


@pytest.mark.parametrize(
    ("targets_value", "changes"),
    [
        ((), {"Comment": "x"}),
        ((GrowthCultivationTarget(PlateId("plate-1"), "v1"),) * 2, {"Comment": "x"}),
        ((GrowthCultivationTarget(PlateId(""), "v1"),), {"Comment": "x"}),
        ((GrowthCultivationTarget(PlateId("plate-1"), ""),), {"Comment": "x"}),
        ((GrowthCultivationTarget(PlateId("plate-1"), "v1"),), {}),
        ((GrowthCultivationTarget(PlateId("plate-1"), "v1"),), {"Project": "x"}),
        ((GrowthCultivationTarget(PlateId("plate-1"), "v1"),), {"Comment": 1}),
        (
            (GrowthCultivationTarget(PlateId("plate-1"), "v1"),),
            {"InoculationDateTime": "09/12/2026"},
        ),
    ],
)
def test_invalid_semantic_inputs_are_structured_domain_errors(
    targets_value: tuple[GrowthCultivationTarget, ...], changes: Mapping[str, str]
) -> None:
    repository = FakeRepository()
    with pytest.raises(DomainValidationError) as raised:
        UpdateBulkGrowthCultivationMetadataService(repository).execute(
            EDITOR, targets_value, changes
        )
    assert raised.value.primary_code is IssueCode.INVALID_VALUE


def test_maximum_target_count_and_shared_field_contract() -> None:
    assert CULTIVATION_SHARED_FIELDS == (
        "CultivationReplicateScope",
        "CultivationConditionFields",
        "Team_Code",
        "CultivationSystemCode",
        "CultivationExperiment",
        "InoculationDateTime",
        "ProgramMetric",
        "EquipmentMakeModel",
        "Objective",
        "CultivationProtocol",
        "SampleAnalysisProtocol",
        "Comment",
    )
    repository = FakeRepository()
    too_many = tuple(
        GrowthCultivationTarget(PlateId(f"plate-{index}"), "version") for index in range(501)
    )
    with pytest.raises(DomainValidationError, match="500"):
        UpdateBulkGrowthCultivationMetadataService(repository).execute(
            EDITOR, too_many, {"Comment": "x"}
        )


@pytest.mark.parametrize("failure", ["late conflict", "provenance"])
def test_late_write_or_provenance_failure_rolls_back_whole_batch(failure: str) -> None:
    repository = FakeRepository()
    if failure == "late conflict":
        repository.late_conflict_for = PlateId("plate-2")
    else:
        repository.fail_provenance = True

    with pytest.raises(RuntimeError):
        UpdateBulkGrowthCultivationMetadataService(repository).execute(
            EDITOR, targets(repository), {"Comment": "changed"}, fill_missing_only=False
        )

    assert repository.rows[PlateId("plate-1")]["updated_at"] == "v1"
    assert repository.rows[PlateId("plate-2")]["updated_at"] == "v2"
    assert registry(repository, "plate-1")["Comment"] == "  "
    assert repository.events == []


def row(plate_id: str, updated_at: str, plate_custom_json: dict[str, object]) -> dict[str, object]:
    return {
        "plate_id": plate_id,
        "experiment_name": f"Experiment {plate_id}",
        "plate_name": f"Plate {plate_id}",
        "updated_at": updated_at,
        "plate_custom_json": plate_custom_json,
    }


def targets(repository: FakeRepository) -> tuple[GrowthCultivationTarget, ...]:
    return tuple(
        GrowthCultivationTarget(plate_id, str(repository.rows[plate_id]["updated_at"]))
        for plate_id in (PlateId("plate-1"), PlateId("plate-2"))
    )


def custom(repository: FakeRepository, plate_id: str) -> dict[str, object]:
    value = repository.rows[PlateId(plate_id)]["plate_custom_json"]
    assert isinstance(value, dict)
    return value


def registry(repository: FakeRepository, plate_id: str) -> dict[str, object]:
    return cast(dict[str, object], custom(repository, plate_id)["cultivation_registry"])
