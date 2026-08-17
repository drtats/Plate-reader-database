"""Behavior tests for staged individual-well Growth comparison selection."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from plate_reader.application.services.growth_comparison import (
    GrowthComparisonPlate,
    GrowthComparisonWell,
)
from plate_reader.ui.growth_comparison import (
    _comparison_plate_ids,
    _selected_wells,
    _well_key,
    _well_table,
)


def test_well_table_uses_plate_and_well_identity_as_its_hidden_stable_key() -> None:
    first = GrowthComparisonWell("plate-a", "well-a", "A1", display_name="Wild type")
    second = GrowthComparisonWell("plate-b", "well-b", "B2", display_name="Mutant")
    plates = (
        GrowthComparisonPlate("plate-a", (first,), "Experiment A", "Plate A"),
        GrowthComparisonPlate("plate-b", (second,), "Experiment B", "Plate B"),
    )

    table, wells_by_key = _well_table((first, second), plates)
    selected = table.copy()
    selected.loc[_well_key(second), "Select"] = True

    assert "well_key" not in table.columns
    assert table.loc[_well_key(first), "Experiment"] == "Experiment A"
    assert table.loc[_well_key(second), "Plate"] == "Plate B"
    assert _selected_wells(selected, wells_by_key) == (second,)


def test_comparison_plate_ids_require_library_owned_tuple_of_nonblank_values() -> None:
    assert _comparison_plate_ids((" plate-a ", "plate-b")) == ("plate-a", "plate-b")
    assert _comparison_plate_ids(["plate-a", "plate-b"]) == ()
    assert _comparison_plate_ids(("", " ")) == ()


def test_page_indexes_once_adds_wells_without_raw_reads_and_renders_only_on_request() -> None:
    app = _comparison_page_app().run()

    assert app.session_state["index_calls"] == 1
    assert "raw_load_calls" not in app.session_state
    assert _button(app, "Search wells") is not None

    _button(app, "Search wells").click().run()
    assert app.session_state["index_calls"] == 1
    assert app.session_state["growth_comparison_search_result"].total == 2
    assert "raw_load_calls" not in app.session_state

    _button(app, "Add all displayed").click().run()
    assert tuple(_well_key(well) for well in app.session_state["growth_comparison_basket"]) == (
        "plate-a:well-a",
        "plate-b:well-b",
    )
    assert "raw_load_calls" not in app.session_state

    _button(app, "Render comparison curves").click().run()
    assert app.session_state["raw_load_calls"] == 2
    assert app.session_state["growth_comparison_plot_result"].well_count == 2
    assert len(app.get("plotly_chart")) == 1


def test_search_never_clears_existing_basket_and_duplicate_add_is_ignored() -> None:
    app = _comparison_page_app().run()
    _button(app, "Search wells").click().run()
    _button(app, "Add all displayed").click().run()
    _button(app, "Add all displayed").click().run()

    assert tuple(_well_key(well) for well in app.session_state["growth_comparison_basket"]) == (
        "plate-a:well-a",
        "plate-b:well-b",
    )
    assert app.session_state["index_calls"] == 1


def test_clear_selection_empties_basket_and_invalidates_a_previous_plot() -> None:
    app = _comparison_page_app().run()
    _button(app, "Search wells").click().run()
    _button(app, "Add all displayed").click().run()
    _button(app, "Render comparison curves").click().run()

    _button(app, "Clear selection").click().run()

    assert app.session_state["growth_comparison_basket"] == ()
    assert "growth_comparison_plot_result" not in app.session_state


def test_successive_source_searches_add_individual_checked_wells_without_clearing_basket() -> None:
    app = _comparison_page_app(auto_select_first=True).run()
    app.multiselect[0].set_value(["plate-a"])
    _button(app, "Search wells").click().run()
    _button(app, "Add selected wells").click().run()

    app.multiselect[0].set_value(["plate-b"])
    _button(app, "Search wells").click().run()
    _button(app, "Add selected wells").click().run()

    assert tuple(_well_key(well) for well in app.session_state["growth_comparison_basket"]) == (
        "plate-a:well-a",
        "plate-b:well-b",
    )
    assert app.session_state["index_calls"] == 1
    assert "raw_load_calls" not in app.session_state


def test_remove_checked_well_keeps_other_wells_and_invalidates_existing_plot() -> None:
    app = _comparison_page_app(auto_select_first=True).run()
    app.multiselect[0].set_value(["plate-a"])
    _button(app, "Search wells").click().run()
    _button(app, "Add selected wells").click().run()
    app.multiselect[0].set_value(["plate-b"])
    _button(app, "Search wells").click().run()
    _button(app, "Add selected wells").click().run()
    _button(app, "Render comparison curves").click().run()

    _button(app, "Remove selected").click().run()

    assert tuple(_well_key(well) for well in app.session_state["growth_comparison_basket"]) == (
        "plate-b:well-b",
    )
    assert "growth_comparison_plot_result" not in app.session_state


def test_invalid_concentration_range_without_one_unit_is_safe_and_does_not_search() -> None:
    app = _comparison_page_app().run()
    app.text_input[1].input("0.5")
    _button(app, "Search wells").click().run()

    assert any("exactly one concentration unit" in item.value for item in app.error)
    assert "growth_comparison_search_result" not in app.session_state
    assert "growth_comparison_basket" not in app.session_state
    assert "raw_load_calls" not in app.session_state


def test_empty_source_run_filter_is_safe_and_does_not_search_every_plate() -> None:
    app = _comparison_page_app().run()
    app.multiselect[0].set_value([])
    _button(app, "Search wells").click().run()

    assert any("at least one source run" in item.value for item in app.error)
    assert "growth_comparison_search_result" not in app.session_state


def test_source_set_change_clears_basket_and_stale_plot_without_loading_raw_data() -> None:
    app = _comparison_page_app(include_source_change=True).run()
    _button(app, "Search wells").click().run()
    _button(app, "Add all displayed").click().run()
    _button(app, "Render comparison curves").click().run()

    assert "growth_comparison_plot_result" in app.session_state
    _button(app, "Change source runs").click().run()

    assert "growth_comparison_basket" not in app.session_state
    assert "growth_comparison_plot_result" not in app.session_state
    assert app.session_state["raw_load_calls"] == 2


def test_page_stops_invalid_or_duplicate_library_source_sets_before_indexing() -> None:
    invalid = _comparison_page_app(plate_ids=("plate-a",)).run()
    duplicate = _comparison_page_app(plate_ids=("plate-a", "plate-a")).run()

    assert any("Select at least two runs" in item.value for item in invalid.info)
    assert "index_calls" not in invalid.session_state
    assert any("unique plate IDs" in item.value for item in duplicate.error)
    assert "index_calls" not in duplicate.session_state


def _button(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def _comparison_page_app(
    *,
    plate_ids: tuple[str, ...] = ("plate-a", "plate-b"),
    include_source_change: bool = False,
    auto_select_first: bool = False,
) -> AppTest:
    source_change = (
        """
if st.button("Change source runs"):
    st.session_state["growth_comparison_plate_ids"] = ("plate-a", "plate-c")
"""
        if include_source_change
        else ""
    )
    auto_select = (
        """
_original_data_editor = st.data_editor

def _select_first_row(frame, **_kwargs):
    selected = frame.copy()
    column = "Select" if "Select" in selected.columns else "Remove"
    selected.iloc[0, selected.columns.get_loc(column)] = True
    return selected

st.data_editor = _select_first_row
"""
        if auto_select_first
        else ""
    )
    restore_data_editor = "st.data_editor = _original_data_editor" if auto_select_first else "pass"
    return AppTest.from_string(
        f"""
import streamlit as st

from plate_reader.application.contracts import Actor, AssayType, PlateId, Role, UserId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_comparison import render_growth_comparison


class Repository:
    def user_by_email(self, _email):
        return {{"user_id": "user-1", "role": "editor", "is_active": True}}

    def growth_comparison_wells(self, plate_ids):
        st.session_state["index_calls"] = st.session_state.get("index_calls", 0) + 1
        rows = []
        for plate_id, well_id, position, plate_name in (
            ("plate-a", "well-a", "A1", "Plate A"),
            ("plate-b", "well-b", "B1", "Plate B"),
            ("plate-c", "well-c", "C1", "Plate C"),
        ):
            if plate_id in plate_ids:
                rows.append({{
                    "plate_id": plate_id,
                    "well_id": well_id,
                    "position": position,
                    "experiment_name": "Experiment",
                    "plate_name": plate_name,
                    "display_name": f"sample {{position}}",
                    "strain": "PAO1",
                    "treatment": "Ciprofloxacin",
                    "concentration": 1,
                    "concentration_unit": "ug/mL",
                    "medium": "MHB",
                    "replicate": 1,
                    "grouping_label": "drug",
                    "inoculum_size": 5,
                    "inoculum_unit": "log CFU/mL",
                    "is_blank": False,
                }})
        return rows

    def load_plate(self, plate_id):
        st.session_state["raw_load_calls"] = st.session_state.get("raw_load_calls", 0) + 1
        key = str(plate_id)
        well_id = {{"plate-a": "well-a", "plate-b": "well-b", "plate-c": "well-c"}}[key]
        position = {{"plate-a": "A1", "plate-b": "B1", "plate-c": "C1"}}[key]
        return PlateSnapshot(
            plate_id=PlateId(key),
            metadata={{"assay_type": AssayType.GROWTH}},
            wells=({{"well_id": well_id, "position": position, "display_name": position}},),
            raw_observations=({{
                "well_id": well_id,
                "time_index": 0,
                "elapsed_microseconds": 0,
                "channel": "od600",
                "value_raw": 0.2,
            }},),
            revisions=(),
        )

    def plate_cache_token(self, plate_id):
        return f"token-{{plate_id}}"


st.session_state.setdefault("growth_comparison_plate_ids", {plate_ids!r})
{source_change}
{auto_select}
context = AppContext(
    Repository(), Actor(UserId("user-1"), "user@example.com", Role.EDITOR)
)
try:
    render_growth_comparison(context)
finally:
    {restore_data_editor}
"""
    )
