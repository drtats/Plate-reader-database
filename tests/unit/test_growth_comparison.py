from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from plate_reader.application.contracts import Actor, AssayType, PlateId, Role, UserId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.authorization import AuthorizationError
from plate_reader.application.services.growth_comparison import (
    DEFAULT_GROWTH_COMPARISON_FIELDS,
    FindCommonGrowthConditionsService,
    GrowthComparisonMatch,
    GrowthComparisonMatchField,
    GrowthComparisonPlate,
    GrowthComparisonWell,
    LoadGrowthComparisonConditionsService,
    LoadGrowthComparisonPlotService,
    find_common_growth_conditions,
)

ACTOR = Actor(UserId("user-1"), "user@example.invalid", Role.VIEWER)


def _active_user() -> dict[str, object]:
    return {
        "user_id": "user-1",
        "email": ACTOR.email,
        "role": "viewer",
        "is_active": 1,
    }


@dataclass
class GrowthComparisonRepositoryStub:
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
        "experiment_name": "Experiment 1",
        "plate_name": f"Plate {plate_id}",
        "strain": "PAO1",
        "treatment": "Ciprofloxacin",
        "concentration": 1.0,
        "concentration_unit": "ug/mL",
        "medium": "MHB",
        "replicate": 1,
        "is_blank": 0,
    }
    values.update(changes)
    return values


def _plate(plate_id: str, *wells: GrowthComparisonWell) -> GrowthComparisonPlate:
    return GrowthComparisonPlate(plate_id, wells)


def _well(
    plate_id: str,
    well_id: str,
    position: str,
    **changes: object,
) -> GrowthComparisonWell:
    values: dict[str, object] = {
        "strain": "PAO1",
        "treatment": "Ciprofloxacin",
        "concentration": 1.0,
        "concentration_unit": "ug/mL",
        "medium": "MHB",
        "replicate": 1,
    }
    values.update(changes)
    return GrowthComparisonWell(plate_id, well_id, position, **values)  # type: ignore[arg-type]


def _plot_snapshot(
    plate_id: str, well_id: str, position: str, *, growth: bool = True
) -> PlateSnapshot:
    return PlateSnapshot(
        plate_id=PlateId(plate_id),
        metadata={"assay_type": AssayType.GROWTH if growth else AssayType.MIC},
        wells=(
            {
                "well_id": well_id,
                "position": position,
                "display_name": f"sample {position}",
                "raw_label": position,
            },
        ),
        raw_observations=(
            {
                "well_id": well_id,
                "time_index": 0,
                "elapsed_microseconds": 0,
                "channel": "od600",
                "value_raw": 0.25,
            },
        ),
        revisions=(),
    )


def test_matches_common_settings_and_retains_readable_display() -> None:
    result = find_common_growth_conditions(
        (
            _plate("plate-a", _well("plate-a", "a1", "A1", strain=" PAO1 ")),
            _plate("plate-b", _well("plate-b", "b1", "B1", strain="pao1", concentration=1)),
        )
    )

    assert result.match_fields == DEFAULT_GROWTH_COMPARISON_FIELDS
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.condition.strain == "pao1"
    assert match.condition.concentration == Decimal("1")
    assert match.display.strain == "PAO1"
    assert match.display.concentration == "1"
    assert tuple(item.plate_id for item in match.plate_matches) == ("plate-a", "plate-b")
    assert tuple(item.wells[0].position for item in match.plate_matches) == ("A1", "B1")


def test_returns_only_the_intersection_across_every_selected_plate() -> None:
    result = find_common_growth_conditions(
        (
            _plate(
                "plate-a",
                _well("plate-a", "a1", "A1", concentration=1),
                _well("plate-a", "a2", "A2", concentration=2),
            ),
            _plate(
                "plate-b",
                _well("plate-b", "b1", "A1", concentration=1),
                _well("plate-b", "b2", "A2", concentration=3),
            ),
            _plate("plate-c", _well("plate-c", "c1", "A1", concentration=1)),
        )
    )

    assert [match.condition.concentration for match in result.matches] == [Decimal("1")]


def test_returns_no_matches_when_plates_have_no_common_condition() -> None:
    result = find_common_growth_conditions(
        (
            _plate("plate-a", _well("plate-a", "a1", "A1", concentration=1)),
            _plate("plate-b", _well("plate-b", "b1", "A1", concentration=2)),
        )
    )

    assert result.matches == ()
    assert [item.excluded_well_count for item in result.exclusions] == [0, 0]


def test_concentration_units_are_an_inseparable_part_of_a_concentration_match() -> None:
    result = find_common_growth_conditions(
        (
            _plate("plate-a", _well("plate-a", "a1", "A1", concentration_unit="ug/mL")),
            _plate("plate-b", _well("plate-b", "b1", "A1", concentration_unit="mg/mL")),
        )
    )

    assert result.matches == ()


def test_missing_required_metadata_and_blanks_are_excluded_per_plate() -> None:
    result = find_common_growth_conditions(
        (
            _plate(
                "plate-a",
                _well("plate-a", "a1", "A1", treatment=None),
                _well("plate-a", "a2", "A2", is_blank=True),
                _well("plate-a", "a3", "A3", concentration_unit=None),
                _well("plate-a", "a4", "A4"),
            ),
            _plate("plate-b", _well("plate-b", "b1", "A1")),
        )
    )

    exclusion = result.exclusions[0]
    assert exclusion.blank_well_count == 1
    assert exclusion.missing_required_metadata_count == 2
    assert exclusion.excluded_well_count == 3
    assert len(result.matches) == 1
    assert result.matches[0].plate_matches[0].wells[0].well_id == "a4"


def test_replicates_remain_separate_and_are_ordered_by_replicate_then_position() -> None:
    result = find_common_growth_conditions(
        (
            _plate(
                "plate-a",
                _well("plate-a", "a3", "B1", replicate=2),
                _well("plate-a", "a2", "A2", replicate=1),
                _well("plate-a", "a1", "A1", replicate=1),
            ),
            _plate("plate-b", _well("plate-b", "b1", "A1")),
        )
    )

    wells = result.matches[0].plate_matches[0].wells
    assert [(well.replicate, well.position) for well in wells] == [(1, "A1"), (1, "A2"), (2, "B1")]


def test_medium_can_be_added_as_an_exact_match_field() -> None:
    result = find_common_growth_conditions(
        (
            _plate("plate-a", _well("plate-a", "a1", "A1", medium="MHB")),
            _plate("plate-b", _well("plate-b", "b1", "A1", medium="LB")),
        ),
        (*DEFAULT_GROWTH_COMPARISON_FIELDS, GrowthComparisonMatchField.MEDIUM),
    )

    assert result.matches == ()


@pytest.mark.parametrize(
    ("plates", "fields", "message"),
    (
        ((), DEFAULT_GROWTH_COMPARISON_FIELDS, "at least two plates"),
        (
            (_plate("plate-a", _well("plate-a", "a1", "A1")),),
            DEFAULT_GROWTH_COMPARISON_FIELDS,
            "at least two plates",
        ),
        (
            (
                _plate("plate-a", _well("plate-a", "a1", "A1")),
                _plate("plate-a", _well("plate-a", "a2", "A2")),
            ),
            DEFAULT_GROWTH_COMPARISON_FIELDS,
            "must be unique",
        ),
        (
            (
                _plate("plate-a", _well("plate-a", "a1", "A1")),
                _plate("plate-b", _well("plate-b", "b1", "A1")),
            ),
            (),
            "at least one",
        ),
        (
            (
                _plate("plate-a", _well("plate-a", "a1", "A1")),
                _plate("plate-b", _well("plate-b", "b1", "A1")),
            ),
            (GrowthComparisonMatchField.STRAIN, GrowthComparisonMatchField.STRAIN),
            "cannot be repeated",
        ),
    ),
)
def test_validates_plate_and_match_field_selection(
    plates: tuple[GrowthComparisonPlate, ...],
    fields: tuple[GrowthComparisonMatchField, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        FindCommonGrowthConditionsService().execute(plates, fields)


def test_loads_condition_only_rows_once_and_preserves_requested_plate_order() -> None:
    repository = GrowthComparisonRepositoryStub(
        rows=(
            _row("plate-a", "a2", "A2", replicate="2", concentration="1.00"),
            _row("plate-b", "b1", "B1", experiment_name=" Experiment 2 ", plate_name=" B "),
            _row("plate-a", "a1", "A1", replicate=1),
        )
    )

    result = LoadGrowthComparisonConditionsService(repository).execute(
        ACTOR, (PlateId("plate-b"), PlateId("plate-a"))
    )

    assert repository.calls == [(PlateId("plate-b"), PlateId("plate-a"))]
    assert [plate.plate_id for plate in result] == ["plate-b", "plate-a"]
    assert result[0].experiment_name == "Experiment 2"
    assert result[0].plate_name == "B"
    assert [(well.well_id, well.replicate) for well in result[1].wells] == [
        ("a2", 2),
        ("a1", 1),
    ]
    assert result[1].wells[0].concentration == Decimal("1.00")


def test_loader_rejects_unauthorized_actor_without_querying_conditions() -> None:
    repository = GrowthComparisonRepositoryStub(rows=(), user=None)

    with pytest.raises(AuthorizationError, match="not registered"):
        LoadGrowthComparisonConditionsService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-b"))
        )

    assert repository.calls == []


def test_loader_validates_requested_plate_ids_before_querying_conditions() -> None:
    repository = GrowthComparisonRepositoryStub(rows=())

    with pytest.raises(ValueError, match="must be unique"):
        LoadGrowthComparisonConditionsService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-a"))
        )

    assert repository.calls == []


def test_loader_rejects_missing_requested_plate_rows() -> None:
    repository = GrowthComparisonRepositoryStub(rows=(_row("plate-a", "a1", "A1"),))

    with pytest.raises(ValueError, match="did not return requested plates: plate-b"):
        LoadGrowthComparisonConditionsService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-b"))
        )

    assert repository.calls == [(PlateId("plate-a"), PlateId("plate-b"))]


def test_loader_rejects_unexpected_plate_rows() -> None:
    repository = GrowthComparisonRepositoryStub(
        rows=(
            _row("plate-a", "a1", "A1"),
            _row("plate-b", "b1", "A1"),
            _row("unrequested", "x1", "A1"),
        )
    )

    with pytest.raises(ValueError, match="unexpected plate: unrequested"):
        LoadGrowthComparisonConditionsService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-b"))
        )


def test_loader_rejects_inconsistent_readable_names_within_a_plate() -> None:
    repository = GrowthComparisonRepositoryStub(
        rows=(
            _row("plate-a", "a1", "A1", plate_name="Plate A"),
            _row("plate-a", "a2", "A2", plate_name="Other plate A"),
            _row("plate-b", "b1", "A1"),
        )
    )

    with pytest.raises(ValueError, match="inconsistent names for plate: plate-a"):
        LoadGrowthComparisonConditionsService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-b"))
        )


def test_plot_loader_loads_each_plate_once_and_makes_series_positions_unique() -> None:
    plates = (
        GrowthComparisonPlate(
            "plate-a",
            (_well("plate-a", "a1", "A1"),),
            experiment_name="Experiment A",
            plate_name="Plate A",
        ),
        GrowthComparisonPlate(
            "plate-b",
            (_well("plate-b", "b1", "A1"),),
            experiment_name="Experiment B",
            plate_name="Plate B",
        ),
    )
    matches = find_common_growth_conditions(plates).matches
    repository = GrowthComparisonPlotRepositoryStub(
        snapshots={
            "plate-a": _plot_snapshot("plate-a", "a1", "A1"),
            "plate-b": _plot_snapshot("plate-b", "b1", "A1"),
        },
        tokens={"plate-a": "token-a", "plate-b": "token-b"},
    )

    result = LoadGrowthComparisonPlotService(repository).execute(ACTOR, plates, matches)

    assert repository.load_calls == [PlateId("plate-a"), PlateId("plate-b")]
    assert repository.token_calls == [PlateId("plate-a"), PlateId("plate-b")]
    assert [point.position for point in result.plot_data.points] == ["plate-a:A1", "plate-b:A1"]
    assert result.plot_data.points[0].label == (
        "Experiment A | Plate A | A1 | PAO1; Ciprofloxacin; 1 ug/mL"
    )
    assert result.plot_data.correction_requested is False
    assert result.plate_count == result.well_count == 2
    assert result.cache_key.startswith("growth-comparison:")


def test_plot_loader_rejects_empty_or_malformed_match_selection_before_load() -> None:
    plates = (
        _plate("plate-a", _well("plate-a", "a1", "A1")),
        _plate("plate-b", _well("plate-b", "b1", "A1")),
    )
    valid_match = find_common_growth_conditions(plates).matches[0]
    malformed = GrowthComparisonMatch(
        valid_match.condition,
        valid_match.display,
        (valid_match.plate_matches[0],),
    )
    repository = GrowthComparisonPlotRepositoryStub(snapshots={}, tokens={})
    service = LoadGrowthComparisonPlotService(repository)

    with pytest.raises(ValueError, match="at least one common"):
        service.execute(ACTOR, plates, ())
    with pytest.raises(ValueError, match="does not belong"):
        service.execute(ACTOR, plates, (malformed,))

    assert repository.load_calls == []
    assert repository.token_calls == []


def test_plot_loader_rejects_missing_plate_and_cache_token() -> None:
    plates = (
        _plate("plate-a", _well("plate-a", "a1", "A1")),
        _plate("plate-b", _well("plate-b", "b1", "A1")),
    )
    matches = find_common_growth_conditions(plates).matches
    missing_plate = GrowthComparisonPlotRepositoryStub(
        snapshots={"plate-a": _plot_snapshot("plate-a", "a1", "A1")},
        tokens={"plate-a": "token-a"},
    )

    with pytest.raises(LookupError, match="not found: plate-b"):
        LoadGrowthComparisonPlotService(missing_plate).execute(ACTOR, plates, matches)

    missing_token = GrowthComparisonPlotRepositoryStub(
        snapshots={
            "plate-a": _plot_snapshot("plate-a", "a1", "A1"),
            "plate-b": _plot_snapshot("plate-b", "b1", "A1"),
        },
        tokens={"plate-a": "token-a", "plate-b": None},
    )
    with pytest.raises(LookupError, match="cache token not found: plate-b"):
        LoadGrowthComparisonPlotService(missing_token).execute(ACTOR, plates, matches)


def test_plot_loader_rejects_non_growth_snapshot() -> None:
    plates = (
        _plate("plate-a", _well("plate-a", "a1", "A1")),
        _plate("plate-b", _well("plate-b", "b1", "A1")),
    )
    repository = GrowthComparisonPlotRepositoryStub(
        snapshots={
            "plate-a": _plot_snapshot("plate-a", "a1", "A1", growth=False),
            "plate-b": _plot_snapshot("plate-b", "b1", "A1"),
        },
        tokens={"plate-a": "token-a", "plate-b": "token-b"},
    )

    with pytest.raises(ValueError, match="not a growth run: plate-a"):
        LoadGrowthComparisonPlotService(repository).execute(
            ACTOR, plates, find_common_growth_conditions(plates).matches
        )

    assert repository.load_calls == [PlateId("plate-a")]
    assert repository.token_calls == []


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"plate_id": " "}, "plate_id cannot be empty"),
        ({"well_id": " "}, "well_id cannot be empty"),
        ({"position": " "}, "position cannot be empty"),
        ({"replicate": 0}, "replicate must be a positive integer"),
        ({"replicate": True}, "replicate must be a positive integer"),
        ({"concentration": True}, "concentration must be a finite number"),
        ({"concentration": float("nan")}, "concentration must be a finite number"),
    ),
)
def test_well_dto_rejects_invalid_identity_replicate_and_concentration(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "plate_id": "plate-a",
        "well_id": "a1",
        "position": "A1",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        GrowthComparisonWell(**values)  # type: ignore[arg-type]


def test_plate_dto_rejects_empty_mismatched_and_duplicate_wells() -> None:
    well = _well("plate-a", "a1", "A1")

    with pytest.raises(ValueError, match="plate_id cannot be empty"):
        GrowthComparisonPlate(" ", (well,))
    with pytest.raises(ValueError, match="must belong"):
        GrowthComparisonPlate("plate-b", (well,))
    with pytest.raises(ValueError, match="duplicate well_id"):
        GrowthComparisonPlate("plate-a", (well, well))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("concentration", "not-a-number", "invalid concentration"),
        ("concentration", True, "invalid concentration"),
        ("replicate", "two", "invalid replicate"),
        ("replicate", True, "invalid replicate"),
        ("is_blank", "yes", "invalid is_blank"),
        ("well_id", " ", "missing well_id"),
    ),
)
def test_condition_loader_rejects_invalid_condition_row_values(
    field: str, value: object, message: str
) -> None:
    invalid_row = _row("plate-a", "a1", "A1")
    invalid_row[field] = value
    repository = GrowthComparisonRepositoryStub(rows=(invalid_row, _row("plate-b", "b1", "A1")))

    with pytest.raises(ValueError, match=message):
        LoadGrowthComparisonConditionsService(repository).execute(
            ACTOR, (PlateId("plate-a"), PlateId("plate-b"))
        )


def test_matcher_rejects_string_match_field_instead_of_silently_accepting_it() -> None:
    plates = (
        _plate("plate-a", _well("plate-a", "a1", "A1")),
        _plate("plate-b", _well("plate-b", "b1", "A1")),
    )

    with pytest.raises(ValueError, match="must be GrowthComparisonMatchField"):
        FindCommonGrowthConditionsService().execute(plates, ("strain",))  # type: ignore[arg-type]


def test_plot_loader_rejects_selected_position_missing_from_raw_snapshot() -> None:
    plates = (
        _plate("plate-a", _well("plate-a", "a1", "A1")),
        _plate("plate-b", _well("plate-b", "b1", "A1")),
    )
    repository = GrowthComparisonPlotRepositoryStub(
        snapshots={
            "plate-a": _plot_snapshot("plate-a", "a1", "B1"),
            "plate-b": _plot_snapshot("plate-b", "b1", "A1"),
        },
        tokens={"plate-a": "token-a", "plate-b": "token-b"},
    )

    with pytest.raises(ValueError, match="positions are not present in plate plate-a: A1"):
        LoadGrowthComparisonPlotService(repository).execute(
            ACTOR, plates, find_common_growth_conditions(plates).matches
        )

    assert repository.load_calls == [PlateId("plate-a")]
    assert repository.token_calls == [PlateId("plate-a")]
