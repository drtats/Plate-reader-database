"""Cultivation controls preview without writes, then save explicitly."""

from streamlit.testing.v1 import AppTest


def test_cultivation_controls_preview_save_and_invalid_inputs() -> None:
    app = _app().run()
    assert not app.exception
    assert "saved_registry" not in app.session_state
    for widget in app.text_input:
        if widget.label == "Team code":
            widget.set_value("PN")
        elif widget.label == "Cultivation system / experiment code":
            widget.set_value("MP96A")
        elif widget.label == "Objective":
            widget.set_value("Synthetic combination assay")
        elif widget.label == "Inoculation date/time (YYYY-MM-DD HH:MM)":
            widget.set_value("2026-03-31 13:39")
    next(button for button in app.button if button.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert "saved_registry" not in app.session_state
    assert app.dataframe[0].value.iloc[0]["Cultivation"] == "PN-EXP-MG1655-MP96A023R1"
    next(
        button for button in app.button if button.label == "Save cultivation metadata and IDs"
    ).click().run()
    assert not app.exception and not app.error
    assert app.session_state["saved_registry"]["Objective"] == "Synthetic combination assay"
    assert app.session_state["save_count"] == 1
    app.run()
    assert app.session_state["save_count"] == 1
    next(widget for widget in app.text_input if widget.label == "Team code").set_value("")
    next(button for button in app.button if button.label == "Preview cultivation IDs").click().run()
    assert not app.exception
    assert app.error
    assert app.session_state["save_count"] == 1


def _app() -> AppTest:
    return AppTest.from_string(
        """
import streamlit as st
from plate_reader.application.contracts import Actor, AssayType, PlateId, Role, UserId
from plate_reader.application.ports.repositories import PlateSnapshot
from plate_reader.application.services.growth_workflow import GrowthRunView
from plate_reader.ui.context import AppContext
import plate_reader.ui.growth_cultivation as ui

snapshot = PlateSnapshot(PlateId("p"), {
    "assay_type": AssayType.GROWTH, "plate_custom_json": "{}", "updated_at": "v1"
}, ({"well_id": "w1", "position": "A1", "strain": "MG1655", "replicate": 1,
     "custom_json": "{}"},), (), ())
class SaveService:
    def __init__(self, repository): pass
    def execute(self, actor, plate_id, version, registry, assignments):
        st.session_state["saved_registry"] = registry
        st.session_state["save_count"] = st.session_state.get("save_count", 0) + 1
        assert assignments[0].cultivation_run == "23"
        assert version == "v1"
        return snapshot
ui.SaveGrowthCultivationsService = SaveService
original_editor = st.data_editor
def select_well(frame, **kwargs):
    edited = frame.copy()
    edited["Generate"] = True
    edited["Cultivation run number"] = "23"
    return edited
st.data_editor = select_well
try:
    ui.render_growth_cultivations(
        AppContext(object(), Actor(UserId("u"), "test@example.invalid", Role.EDITOR)),
        PlateId("p"), GrowthRunView(snapshot, (), ())
    )
finally:
    st.data_editor = original_editor
""",
        default_timeout=30,
    )
