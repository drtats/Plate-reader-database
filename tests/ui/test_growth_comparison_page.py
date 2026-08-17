"""Focused tests for the condition-only Growth comparison UI helpers."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from plate_reader.application.services.growth_comparison import (
    GrowthComparisonPlate,
    GrowthComparisonWell,
    find_common_growth_conditions,
)
from plate_reader.ui.growth_comparison import (
    _comparison_match_table,
    _comparison_plate_ids,
    _selected_condition_keys,
)


def test_comparison_match_table_uses_stable_hidden_condition_identifiers() -> None:
    result = find_common_growth_conditions(
        (
            GrowthComparisonPlate(
                "plate-a",
                (
                    GrowthComparisonWell(
                        "plate-a", "well-a", "A1", "PAO1", "Ciprofloxacin", 1, "ug/mL"
                    ),
                ),
            ),
            GrowthComparisonPlate(
                "plate-b",
                (
                    GrowthComparisonWell(
                        "plate-b", "well-b", "B1", "pao1", "Ciprofloxacin", 1.0, "ug/mL"
                    ),
                ),
            ),
        )
    )

    table, keys = _comparison_match_table(result)
    selected_table = table.copy()
    selected_table.loc[:, "Select"] = True

    assert len(table.index) == 1
    assert "condition_identifier" not in table.columns
    assert table.iloc[0]["Concentration"] == "1 ug/mL"
    assert _selected_condition_keys(selected_table, keys) == (result.matches[0].condition,)


def test_comparison_plate_ids_require_a_tuple_of_nonblank_values() -> None:
    assert _comparison_plate_ids((" plate-a ", "plate-b")) == ("plate-a", "plate-b")
    assert _comparison_plate_ids(["plate-a", "plate-b"]) == ()
    assert _comparison_plate_ids(("", " ")) == ()


def test_comparison_page_caches_condition_load_and_stages_match_discovery() -> None:
    app = AppTest.from_string(
        """
import streamlit as st

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_comparison import render_growth_comparison


class Repository:
    def user_by_email(self, _email):
        return {"user_id": "user-1", "role": "editor", "is_active": True}

    def growth_comparison_wells(self, plate_ids):
        st.session_state["condition_loader_calls"] = (
            st.session_state.get("condition_loader_calls", 0) + 1
        )
        rows = []
        for plate_id, well_id, position, plate_name in (
            ("plate-a", "well-a", "A1", "Plate A"),
            ("plate-b", "well-b", "B1", "Plate B"),
        ):
            if plate_id in plate_ids:
                rows.append(
                    {
                        "plate_id": plate_id,
                        "well_id": well_id,
                        "position": position,
                        "experiment_name": "Experiment",
                        "plate_name": plate_name,
                        "strain": "PAO1",
                        "treatment": "Ciprofloxacin",
                        "concentration": 1,
                        "concentration_unit": "ug/mL",
                        "medium": "MHB",
                        "replicate": 1,
                        "is_blank": False,
                    }
                )
        return rows


st.session_state.setdefault("growth_comparison_plate_ids", ("plate-a", "plate-b"))
context = AppContext(
    Repository(), Actor(UserId("user-1"), "user@example.com", Role.EDITOR)
)
render_growth_comparison(context)
"""
    ).run()

    assert app.session_state["condition_loader_calls"] == 1
    assert app.header[0].value == "Plate Comparison"
    assert _button(app, "Find common settings") is not None

    _button(app, "Find common settings").click().run()

    assert app.session_state["condition_loader_calls"] == 1
    assert len(app.session_state["growth_comparison_result"].matches) == 1
    assert _button(app, "Render comparison curves") is not None


def test_comparison_page_reports_no_matches_and_per_plate_exclusions() -> None:
    app = _comparison_page_app(
        [
            _condition_row("plate-a", "well-a", "A1", "Plate A", concentration=1, is_blank=True),
            _condition_row("plate-a", "well-a2", "A2", "Plate A", concentration=1),
            _condition_row("plate-b", "well-b", "B1", "Plate B", concentration=2),
        ]
    )

    _button(app, "Find common settings").click().run()

    assert not app.get("data_editor")
    assert any("No common settings were found" in item.value for item in app.info)
    assert any("Plate A (plate-a): 1 blank" in item.value for item in app.caption)
    assert any("Plate B (plate-b): 0 blank" in item.value for item in app.caption)


def test_comparison_page_stops_invalid_or_duplicate_plate_selections_before_loading() -> None:
    invalid = _comparison_page_app([], plate_ids=("plate-a",))
    duplicate = _comparison_page_app([], plate_ids=("plate-a", "plate-a"))

    assert any("Select at least two runs" in item.value for item in invalid.info)
    assert "condition_loader_calls" not in invalid.session_state
    assert any("unique plate IDs" in item.value for item in duplicate.error)
    assert "condition_loader_calls" not in duplicate.session_state


def test_comparison_page_displays_loader_errors_without_stale_matches() -> None:
    app = _comparison_page_app([], loader_error=True)

    assert any(
        "Unable to load selected run conditions: loader unavailable" in item.value
        for item in app.error
    )
    assert app.session_state["condition_loader_calls"] == 1
    assert not app.get("data_editor")


def test_comparison_plot_display_renders_only_for_its_selected_plate_ids() -> None:
    visible = _comparison_plot_app(("plate-a", "plate-b"))
    stale = _comparison_plot_app(("plate-a", "plate-c"))

    assert [item.value for item in visible.subheader] == ["Comparison curves"]
    assert any("2 plates · 2 wells · raw values" in item.value for item in visible.caption)
    assert len(visible.get("plotly_chart")) == 1
    assert not stale.subheader
    assert not stale.get("plotly_chart")


def _button(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def _condition_row(
    plate_id: str,
    well_id: str,
    position: str,
    plate_name: str,
    *,
    concentration: int,
    is_blank: bool = False,
) -> dict[str, object]:
    return {
        "plate_id": plate_id,
        "well_id": well_id,
        "position": position,
        "experiment_name": "Experiment",
        "plate_name": plate_name,
        "strain": "PAO1",
        "treatment": "Ciprofloxacin",
        "concentration": concentration,
        "concentration_unit": "ug/mL",
        "medium": "MHB",
        "replicate": 1,
        "is_blank": is_blank,
    }


def _comparison_page_app(
    rows: list[dict[str, object]],
    *,
    plate_ids: tuple[str, ...] = ("plate-a", "plate-b"),
    loader_error: bool = False,
) -> AppTest:
    loader_body = "raise RuntimeError('loader unavailable')" if loader_error else f"return {rows!r}"
    return AppTest.from_string(
        f"""
import streamlit as st

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_comparison import render_growth_comparison


class Repository:
    def user_by_email(self, _email):
        return {{"user_id": "user-1", "role": "editor", "is_active": True}}

    def growth_comparison_wells(self, _plate_ids):
        st.session_state["condition_loader_calls"] = (
            st.session_state.get("condition_loader_calls", 0) + 1
        )
        {loader_body}


st.session_state["growth_comparison_plate_ids"] = {plate_ids!r}
context = AppContext(
    Repository(), Actor(UserId("user-1"), "user@example.com", Role.EDITOR)
)
render_growth_comparison(context)
"""
    ).run()


def _comparison_plot_app(plate_ids: tuple[str, str]) -> AppTest:
    return AppTest.from_string(
        f"""
import streamlit as st

from plate_reader.application.services.growth_comparison import GrowthComparisonPlotResult
from plate_reader.application.services.growth_plotting import GrowthPlotData, GrowthPlotPoint
from plate_reader.ui.growth_comparison import _render_comparison_plot


st.session_state["growth_comparison_plot_result"] = GrowthComparisonPlotResult(
    GrowthPlotData(
        (
            GrowthPlotPoint("plate-a:A1", "Plate A A1", 0.0, "od600", 0.1, 0.1, None, False),
            GrowthPlotPoint("plate-b:B1", "Plate B B1", 0.0, "od600", 0.2, 0.2, None, False),
        ),
        (),
        False,
    ),
    "comparison-cache-key",
    2,
    2,
)
st.session_state["growth_comparison_plot_plate_ids"] = ("plate-a", "plate-b")
_render_comparison_plot({plate_ids!r})
"""
    ).run()
