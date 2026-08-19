from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

import pytest

from plate_reader.application.contracts import Actor, AssayType, PlateId, Role, UserId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import AuthorizationError
from plate_reader.application.services.growth_comparison import (
    GrowthComparisonPlate,
    GrowthComparisonWell,
    GrowthWellSearchFilter,
    GrowthWellSearchResult,
    LoadGrowthComparisonPlotService,
    LoadGrowthComparisonWellIndexService,
    SearchGrowthComparisonWellsService,
    growth_comparison_summary_fields,
)

ACTOR = Actor(UserId("user-1"), "user@example.invalid", Role.VIEWER)


def _active_user() -> dict[str, object]:
    return {"user_id": "user-1", "email": ACTOR.email, "role": "viewer", "is_active": 1}


@dataclass
class GrowthComparisonIndexRepositoryStub:
    rows: tuple[dict[str, object], ...]
    user: dict[str, object] | None = field(default_factory=_active_user)
    calls: list[tuple[PlateId, ...]] = field(default_factory=list)

    def user_by_email(self, email: str) -> dict[str, object] | None:
        assert email == ACTOR.email
        return self.user

    def growth_comparison_wells(
        self, plate_ids: Sequence[PlateId]
    ) -> tuple[dict[str, object], ...]:
        self.calls.append(tuple(plate_ids))
        return self.rows


@dataclass
class GrowthComparisonPlotRepositoryStub:
    snapshots: dict[str, PlateSnapshot | None]
    tokens: dict[str, str | None]
    user: dict[str, object] | None = field(default_factory=_active_user)
    load_calls: list[PlateId] = field(default_factory=list)
    token_calls: list[PlateId] = field(default_factory=list)

    def user_by_email(self, email: str) -> dict[str, object] | None:
        assert email == ACTOR.email
        return self.user

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None:
        self.load_calls.append(plate_id)
        return self.snapshots.get(str(plate_id))

    def plate_cache_token(self, plate_id: PlateId) -> str | None:
        self.token_calls.append(plate_id)
        return self.tokens.get(str(plate_id))


def _row(plate_id: str, well_id: str, position: str, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "plate_id": plate_id,
        "well_id": well_id,
        "position": position,
        "experiment_name": f"Experiment {plate_id}",
        "plate_name": f"Plate {plate_id}",
        "display_name": f"Sample {position}",
        "strain": "PAO1",
        "treatment": "Ciprofloxacin",
        "concentration": 1.0,
        "concentration_unit": "ug/mL",
        "medium": "MHB",
        "replicate": 1,
        "grouping_label": "group A",
        "inoculum_size": 100000,
        "inoculum_unit": "CFU/mL",
        "is_blank": 0,
    }
    values.update(changes)
    return values


def _well(plate_id: str, well_id: str, position: str, **changes: object) -> GrowthComparisonWell:
    values: dict[str, object] = {
        "display_name": f"Sample {position}",
        "strain": "PAO1",
        "treatment": "Ciprofloxacin",
        "concentration": Decimal("1"),
        "concentration_unit": "ug/mL",
        "medium": "MHB",
        "replicate": 1,
        "grouping_label": "group A",
        "inoculum_size": Decimal("100000"),
        "inoculum_unit": "CFU/mL",
    }
    values.update(changes)
    return GrowthComparisonWell(plate_id, well_id, position, **values)  # type: ignore[arg-type]


def _plate(plate_id: str, *wells: GrowthComparisonWell) -> GrowthComparisonPlate:
    return GrowthComparisonPlate(
        plate_id,
        tuple(wells),
        experiment_name=f"Experiment {plate_id}",
        plate_name=f"Plate {plate_id}",
    )


def _plot_snapshot(
    plate_id: str, *wells: GrowthComparisonWell, growth: bool = True
) -> PlateSnapshot:
    return PlateSnapshot(
        plate_id=PlateId(plate_id),
        metadata={"assay_type": AssayType.GROWTH if growth else AssayType.MIC},
        wells=tuple(
            {
                "well_id": well.well_id,
                "position": well.position,
                "display_name": well.display_name,
                "raw_label": well.position,
            }
            for well in wells
        ),
        raw_observations=tuple(
            {
                "well_id": well.well_id,
                "time_index": 0,
                "elapsed_microseconds": 0,
                "channel": "od600",
                "value_raw": 0.25,
            }
            for well in wells
        ),
        revisions=(),
    )


def test_index_loader_uses_one_authorized_metadata_only_call_and_maps_first_class_fields() -> None:
    repository = GrowthComparisonIndexRepositoryStub(
        rows=(
            _row("plate-b", "b1", "B1", concentration="1.00", replicate="2"),
            _row("plate-a", "a1", "A1", display_name=" Named ", grouping_label=" Group "),
        )
    )

    plates = LoadGrowthComparisonWellIndexService(repository).execute(
        ACTOR, (PlateId("plate-b"), PlateId("plate-a"))
    )

    assert repository.calls == [(PlateId("plate-b"), PlateId("plate-a"))]
    assert [plate.plate_id for plate in plates] == ["plate-b", "plate-a"]
    assert plates[0].wells[0].concentration == Decimal("1.00")
    assert plates[0].wells[0].replicate == 2
    assert plates[1].wells[0].display_name == "Named"
    assert plates[1].wells[0].grouping_label == "Group"
    assert plates[1].wells[0].inoculum_size == 100000


@pytest.mark.parametrize(
    ("user", "plate_ids", "message"),
    (
        (None, (PlateId("plate-a"), PlateId("plate-b")), "not registered"),
        (_active_user(), (PlateId("plate-a"),), "at least two plates"),
        (_active_user(), (PlateId("plate-a"), PlateId("plate-a")), "must be unique"),
    ),
)
def test_index_loader_authorizes_and_validates_before_querying(
    user: dict[str, object] | None, plate_ids: tuple[PlateId, ...], message: str
) -> None:
    repository = GrowthComparisonIndexRepositoryStub(rows=(), user=user)

    with pytest.raises((AuthorizationError, ValueError), match=message):
        LoadGrowthComparisonWellIndexService(repository).execute(ACTOR, plate_ids)

    assert repository.calls == []


def test_index_loader_rejects_unavailable_unexpected_and_inconsistent_rows() -> None:
    for rows, message in (
        ((_row("plate-a", "a1", "A1"),), "did not return requested plates: plate-b"),
        (
            (_row("plate-a", "a1", "A1"), _row("plate-b", "b1", "B1"), _row("x", "x1", "C1")),
            "unexpected plate: x",
        ),
        (
            (
                _row("plate-a", "a1", "A1", plate_name="one"),
                _row("plate-a", "a2", "A2", plate_name="two"),
                _row("plate-b", "b1", "B1"),
            ),
            "inconsistent names for plate: plate-a",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            LoadGrowthComparisonWellIndexService(
                GrowthComparisonIndexRepositoryStub(rows=rows)
            ).execute(ACTOR, (PlateId("plate-a"), PlateId("plate-b")))


def test_search_combines_or_within_fields_and_and_across_fields_case_insensitively() -> None:
    plates = (
        _plate(
            "plate-a",
            _well("plate-a", "a3", "B1", strain=" Other ", treatment="Drug A", replicate=2),
            _well("plate-a", "a2", "A2", strain="Mutant", treatment="Drug B", concentration=2),
            _well("plate-a", "a1", "A1", strain=" pao1 ", treatment="Drug A", replicate=2),
        ),
        _plate("plate-b", _well("plate-b", "b1", "A1", strain="WT", treatment="Vehicle")),
    )
    filters = GrowthWellSearchFilter(
        strains=("PAO1", "mutant"),
        treatments=("drug a", "drug b"),
        concentration_min=1,
        concentration_max=1,
        concentration_units=("ug/mL",),
        replicates=(2,),
        inoculum_sizes=(100000,),
        inoculum_units=("CFU/mL",),
    )

    result = SearchGrowthComparisonWellsService().execute(plates, filters)

    assert [(well.plate_id, well.position) for well in result.wells] == [("plate-a", "A1")]
    assert result.total == 1
    assert not result.truncated


def test_search_filters_inoculum_metadata_and_quick_stats_count_actual_wells() -> None:
    wells = (
        _well("plate-a", "a1", "A1", strain="PAO1", treatment="Drug", replicate=1),
        _well("plate-a", "a2", "A2", strain="pao1", treatment="drug", replicate=7),
        _well("plate-b", "b1", "A1", strain="PAO1", treatment="Drug", replicate=3),
        _well(
            "plate-b",
            "b2",
            "A2",
            strain="PAO1",
            treatment="Drug",
            replicate=1,
            inoculum_size=Decimal("200000"),
        ),
    )

    filtered = SearchGrowthComparisonWellsService().execute(
        (_plate("plate-a", *wells[:2]), _plate("plate-b", *wells[2:])),
        GrowthWellSearchFilter(inoculum_sizes=(Decimal("100000.0"),), inoculum_units=("cfu/ML",)),
        ("strain", "treatment", "concentration", "inoculum_size"),
    )

    assert filtered.total == 3
    assert filtered.quick_stats is not None
    assert [field.label for field in filtered.quick_stats.fields] == [
        "Strain",
        "Treatment",
        "Concentration",
        "Inoculum size",
    ]
    assert len(filtered.quick_stats.groups) == 1
    group = filtered.quick_stats.groups[0]
    assert group.values == ("PAO1", "Drug", "1 ug/mL", "100000 CFU/mL")
    assert group.well_count == 3
    assert group.plate_count == 2
    assert "replicate" not in {field.key for field in growth_comparison_summary_fields()}


def test_search_text_source_subset_blanks_and_deterministic_well_order() -> None:
    plates = (
        _plate(
            "plate-a",
            _well("plate-a", "a2", "A2", display_name="target"),
            _well("plate-a", "a1", "A1", display_name="Target blank", is_blank=True),
        ),
        _plate("plate-b", _well("plate-b", "b1", "A1", grouping_label="Target group")),
    )
    service = SearchGrowthComparisonWellsService()

    default_result = service.execute(plates, GrowthWellSearchFilter(text=" target "))
    included_result = service.execute(
        plates,
        GrowthWellSearchFilter(
            source_plate_ids=("plate-a",), text="target", include_blank_wells=True
        ),
    )

    assert [(well.plate_id, well.position) for well in default_result.wells] == [
        ("plate-a", "A2"),
        ("plate-b", "A1"),
    ]
    assert [(well.plate_id, well.position) for well in included_result.wells] == [
        ("plate-a", "A1"),
        ("plate-a", "A2"),
    ]


def test_search_exact_field_filters_handle_missing_values_and_limit_to_first_500() -> None:
    wells = tuple(
        _well("plate-a", f"a{index}", f"A{index}", medium="LB" if index % 2 else "MHB")
        for index in range(1, 502)
    )
    plates = (_plate("plate-a", *wells),)

    result = SearchGrowthComparisonWellsService().execute(
        plates, GrowthWellSearchFilter(media=("MHB", "lb")), ("medium",)
    )

    assert result.total == 501
    assert len(result.wells) == 500
    assert result.truncated
    assert result.wells[0].position == "A1"
    assert result.wells[-1].position == "A500"
    assert result.quick_stats is not None
    assert sum(group.well_count for group in result.quick_stats.groups) == 501


@pytest.mark.parametrize(
    "filters",
    (
        GrowthWellSearchFilter(strains=("PAO1",)),
        GrowthWellSearchFilter(concentration_min=Decimal("1"), concentration_units=("ug/mL",)),
        GrowthWellSearchFilter(concentration_max=Decimal("2"), concentration_units=("ug/mL",)),
        GrowthWellSearchFilter(grouping_labels=("group",)),
        GrowthWellSearchFilter(inoculum_sizes=(Decimal("100000"),)),
        GrowthWellSearchFilter(inoculum_units=("CFU/mL",)),
    ),
)
def test_search_filter_does_not_match_missing_metadata(filters: GrowthWellSearchFilter) -> None:
    missing = _well(
        "plate-a",
        "a1",
        "A1",
        strain=None,
        concentration=None,
        grouping_label=None,
        inoculum_size=None,
        inoculum_unit=None,
    )
    assert (
        SearchGrowthComparisonWellsService().execute((_plate("plate-a", missing),), filters).wells
        == ()
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (lambda: GrowthWellSearchFilter(source_plate_ids=(" ",)), "cannot be empty"),
        (lambda: GrowthWellSearchFilter(replicates=(0,)), "positive integers"),
        (lambda: GrowthWellSearchFilter(inoculum_sizes=(float("nan"),)), "finite number"),
        (lambda: GrowthWellSearchFilter(concentration_min=2, concentration_max=1), "cannot exceed"),
        (lambda: GrowthWellSearchFilter(concentration_min=1), "exactly one concentration unit"),
        (
            lambda: GrowthWellSearchFilter(
                concentration_max=1, concentration_units=("ug/mL", "mg/mL")
            ),
            "exactly one concentration unit",
        ),
        (
            lambda: GrowthWellSearchFilter(include_blank_wells=cast(bool, 1)),
            "must be boolean",
        ),
    ),
)
def test_search_filter_validates_inputs(factory: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()  # type: ignore[operator]


def test_search_rejects_an_unknown_source_plate() -> None:
    with pytest.raises(ValueError, match="outside the supplied index"):
        SearchGrowthComparisonWellsService().execute(
            (_plate("plate-a", _well("plate-a", "a1", "A1")),),
            GrowthWellSearchFilter(source_plate_ids=("missing",)),
        )


def test_quick_stats_reject_unknown_duplicate_or_blank_dimensions() -> None:
    plate = _plate("plate-a", _well("plate-a", "a1", "A1"))
    service = SearchGrowthComparisonWellsService()

    for fields, message in (
        (("replicate",), "Unknown"),
        (("strain", "strain"), "must be unique"),
        ((" ",), "cannot be empty"),
    ):
        with pytest.raises(ValueError, match=message):
            service.execute((plate,), summary_fields=fields)


@pytest.mark.parametrize(
    ("values", "message"),
    (
        ({"plate_id": " "}, "plate_id cannot be empty"),
        ({"well_id": " "}, "well_id cannot be empty"),
        ({"position": " "}, "position cannot be empty"),
        ({"replicate": True}, "replicate must be a positive integer"),
        ({"replicate": 0}, "replicate must be a positive integer"),
        ({"concentration": True}, "concentration must be a finite number"),
        ({"inoculum_size": float("nan")}, "inoculum size must be a finite number"),
    ),
)
def test_well_dto_rejects_invalid_stable_identity_and_numeric_metadata(
    values: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        invalid_values: Any = {"plate_id": "plate-a", "well_id": "a1", "position": "A1"} | values
        GrowthComparisonWell(**invalid_values)


def test_plate_and_search_result_dtos_enforce_their_invariants() -> None:
    a1 = _well("plate-a", "a1", "A1")
    foreign = _well("plate-b", "b1", "A1")
    with pytest.raises(ValueError, match="belong to its containing plate"):
        GrowthComparisonPlate("plate-a", (foreign,))
    with pytest.raises(ValueError, match="duplicate well_id"):
        GrowthComparisonPlate("plate-a", (a1, a1))
    with pytest.raises(ValueError, match="search total is invalid"):
        GrowthWellSearchResult((a1,), 0, False)
    with pytest.raises(ValueError, match="truncation state is inconsistent"):
        GrowthWellSearchResult((a1,), 2, False)
    with pytest.raises(ValueError, match="exceeds its fixed limit"):
        GrowthWellSearchResult((a1,) * 501, 501, False)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"well_id": " "}, "missing well_id"),
        ({"concentration": "not-a-number"}, "invalid concentration"),
        ({"inoculum_size": True}, "invalid inoculum_size"),
        ({"replicate": "not-an-int"}, "invalid replicate"),
        ({"replicate": 1.5}, "invalid replicate"),
        ({"is_blank": "false"}, "invalid is_blank"),
    ),
)
def test_index_loader_rejects_malformed_required_and_optional_row_fields(
    changes: dict[str, object], message: str
) -> None:
    malformed = _row("plate-a", "a1", "A1")
    malformed.update(changes)
    repository = GrowthComparisonIndexRepositoryStub(rows=(malformed, _row("plate-b", "b1", "B1")))

    with pytest.raises(ValueError, match=message):
        LoadGrowthComparisonWellIndexService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-b"))
        )


def test_search_rejects_invalid_filter_and_invalid_plate_index_types() -> None:
    a1 = _well("plate-a", "a1", "A1")
    with pytest.raises(ValueError, match="must be GrowthWellSearchFilter"):
        SearchGrowthComparisonWellsService().execute((_plate("plate-a", a1),), "bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid plate"):
        SearchGrowthComparisonWellsService().execute(("bad",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="plate IDs must be unique"):
        SearchGrowthComparisonWellsService().execute((_plate("plate-a", a1), _plate("plate-a", a1)))


def test_plot_loader_uses_canonical_selected_wells_loads_only_represented_plates_once() -> None:
    a1 = _well("plate-a", "a1", "A1", display_name="Alpha")
    a2 = _well("plate-a", "a2", "A2", display_name="Beta")
    b1 = _well("plate-b", "b1", "A1", display_name="Gamma", strain="Mutant")
    unused = _well("plate-c", "c1", "A1")
    plates = (_plate("plate-a", a1, a2), _plate("plate-b", b1), _plate("plate-c", unused))
    repository = GrowthComparisonPlotRepositoryStub(
        snapshots={
            "plate-a": _plot_snapshot("plate-a", a1, a2),
            "plate-b": _plot_snapshot("plate-b", b1),
        },
        tokens={"plate-a": "token-a", "plate-b": "token-b"},
    )
    tampered_display = GrowthComparisonWell("plate-a", "a1", "not-used", display_name="wrong")

    result = LoadGrowthComparisonPlotService(repository).execute(
        ACTOR, plates, (tampered_display, b1, a2)
    )

    assert repository.load_calls == [PlateId("plate-a"), PlateId("plate-b")]
    assert repository.token_calls == [PlateId("plate-a"), PlateId("plate-b")]
    assert [point.position for point in result.plot_data.points] == [
        "plate-a:a1",
        "plate-a:a2",
        "plate-b:b1",
    ]
    assert result.plot_data.points[0].label == (
        "Experiment plate-a | Plate plate-a | A1 | Alpha; PAO1; Ciprofloxacin; "
        "1 ug/mL; MHB; 100000 CFU/mL; group A; replicate 1"
    )
    assert result.plot_data.correction_requested is False
    assert result.plate_count == 2
    assert result.well_count == 3
    assert result.cache_key.startswith("growth-comparison:")


@pytest.mark.parametrize(
    ("selected", "message"),
    (
        ((), "at least one Growth well"),
        ((_well("plate-a", "a1", "A1"),), "at least two plates"),
        (
            (_well("plate-a", "a1", "A1"), _well("plate-a", "a1", "A1")),
            "must be unique",
        ),
        (
            (_well("plate-a", "other", "A1"), _well("plate-b", "b1", "A1")),
            "does not belong",
        ),
    ),
)
def test_plot_loader_validates_stable_selected_membership(
    selected: tuple[GrowthComparisonWell, ...], message: str
) -> None:
    a1 = _well("plate-a", "a1", "A1")
    b1 = _well("plate-b", "b1", "A1")
    repository = GrowthComparisonPlotRepositoryStub(snapshots={}, tokens={})

    with pytest.raises(ValueError, match=message):
        LoadGrowthComparisonPlotService(repository).execute(
            ACTOR, (_plate("plate-a", a1), _plate("plate-b", b1)), selected
        )

    assert repository.load_calls == []


def test_plot_loader_rejects_missing_snapshot_token_wrong_assay_and_changed_well() -> None:
    a1 = _well("plate-a", "a1", "A1")
    b1 = _well("plate-b", "b1", "A1")
    plates = (_plate("plate-a", a1), _plate("plate-b", b1))
    for repository, message in (
        (
            GrowthComparisonPlotRepositoryStub(
                snapshots={"plate-a": None}, tokens={"plate-a": "token"}
            ),
            "not found",
        ),
        (
            GrowthComparisonPlotRepositoryStub(
                snapshots={
                    "plate-a": _plot_snapshot("plate-a", a1),
                    "plate-b": _plot_snapshot("plate-b", b1),
                },
                tokens={"plate-a": None},
            ),
            "cache token",
        ),
        (
            GrowthComparisonPlotRepositoryStub(
                snapshots={
                    "plate-a": _plot_snapshot("plate-a", a1, growth=False),
                    "plate-b": _plot_snapshot("plate-b", b1),
                },
                tokens={"plate-a": "token-a"},
            ),
            "not a growth run",
        ),
        (
            GrowthComparisonPlotRepositoryStub(
                snapshots={
                    "plate-a": _plot_snapshot("plate-a", _well("plate-a", "a1", "A2")),
                    "plate-b": _plot_snapshot("plate-b", b1),
                },
                tokens={"plate-a": "token-a"},
            ),
            "positions changed",
        ),
    ):
        with pytest.raises((LookupError, ValueError), match=message):
            LoadGrowthComparisonPlotService(repository).execute(ACTOR, plates, (a1, b1))


def test_plot_loader_authorizes_before_raw_reads() -> None:
    a1 = _well("plate-a", "a1", "A1")
    b1 = _well("plate-b", "b1", "A1")
    repository = GrowthComparisonPlotRepositoryStub(snapshots={}, tokens={}, user=None)

    with pytest.raises(AuthorizationError, match="not registered"):
        LoadGrowthComparisonPlotService(repository).execute(
            ACTOR, (_plate("plate-a", a1), _plate("plate-b", b1)), (a1, b1)
        )

    assert repository.load_calls == []
