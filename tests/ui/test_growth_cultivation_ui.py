"""Cultivation controls preview without writes, then save explicitly."""

from streamlit.testing.v1 import AppTest


def test_cultivation_controls_preview_save_and_invalid_inputs() -> None:
    app = _app().run()
    app.selectbox[0].select("Original laboratory format (manual number)").run()
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


def test_default_pattern_uses_simple_number_and_each_wells_strain_and_replicate() -> None:
    app = _app().run()
    assert not app.exception
    number = next(widget for widget in app.text_input if widget.label == "Experiment number")
    assert number.value == "001"
    next(widget for widget in app.text_input if widget.label == "Team code").set_value("PN")
    next(button for button in app.button if button.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert "saved_registry" not in app.session_state
    assert list(app.dataframe[0].value["Cultivation"]) == [
        "PN-EXP-MG1655-001-A01-R1",
        "PN-EXP-MG1655-001-A02-R2",
        "PN-EXP-11_J3-001-B01-R1",
    ]
    next(
        button for button in app.button if button.label == "Save cultivation metadata and IDs"
    ).click().run()
    assert not app.exception and not app.error
    assert app.session_state["saved_registry"]["CultivationExperimentCode"] == "001"
    assert all(a.experiment_code == "001" for a in app.session_state["saved_assignments"])


def test_custom_pattern_validation_prevents_writes() -> None:
    app = _app().run()
    app.selectbox[0].select("Custom pattern").run()
    next(widget for widget in app.text_input if widget.label == "Team code").set_value("PN")
    pattern = next(w for w in app.text_input if w.label == "Custom cultivation ID pattern")
    pattern.set_value("{unknown}-{well}")
    next(
        button for button in app.button if button.label == "Save cultivation metadata and IDs"
    ).click().run()
    assert not app.exception and app.error
    assert "saved_registry" not in app.session_state
    next(w for w in app.text_input if w.label == "Custom cultivation ID pattern").set_value(
        "{team}-{experiment}-{strain}-{well}"
    )
    next(button for button in app.button if button.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert app.dataframe[0].value.iloc[0]["Cultivation"] == "PN-001-MG1655-A01"


def test_unsaved_run_editor_suggests_its_chronological_number() -> None:
    app = _app()
    app.session_state["number_rows"] = (
        {
            "plate_id": "p",
            "record_type": "plate",
            "experiment_date": "2026-09-12",
            "created_at": "2026-09-01T00:00:00",
            "custom_json": {},
        },
        {
            "plate_id": "older",
            "record_type": "plate",
            "experiment_date": "2026-08-01",
            "created_at": "2026-09-20T00:00:00",
            "custom_json": {},
        },
    )
    app.run()
    assert not app.exception
    assert next(w for w in app.text_input if w.label == "Experiment number").value == "002"
    next(w for w in app.text_input if w.label == "Team code").set_value("PN")
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert app.dataframe[0].value.iloc[0]["Cultivation"] == "PN-EXP-MG1655-002-A01-R1"
    assert "saved_registry" not in app.session_state
    app.run()
    assert next(w for w in app.text_input if w.label == "Experiment number").value == "002"


def test_condition_replicates_span_plates_without_changing_local_labels() -> None:
    app = _app()
    app.session_state["replicate_rows"] = (
        {
            "plate_id": "earlier",
            "well_id": "earlier-well",
            "position": "A1",
            "experiment_date": "2026-08-01",
            "created_at": "2026-08-01T00:00:00",
            "strain": "MG1655",
            "medium": "MOPS",
            "replicate": 1,
            "custom_json": {},
        },
    )
    app.run()
    next(w for w in app.selectbox if w.label == "Number the R suffix using").select(
        "Matching conditions across plates"
    ).run()
    next(w for w in app.text_input if w.label == "Team code").set_value("PN")
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    records = app.dataframe[0].value
    assert list(records["Cultivation"])[:2] == [
        "PN-EXP-MG1655-001-A01-R2",
        "PN-EXP-MG1655-001-A02-R3",
    ]
    assert list(records["LocalReplicate"]) == [1, 2, 1]
    assert records.iloc[0]["MatchingWells"] == 3
    assert records.iloc[0]["MatchingPlates"] == 2
    assert "saved_registry" not in app.session_state
    next(w for w in app.text_input if w.label == "Replicate study/group (optional)").set_value(
        "separate-study"
    )
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert app.dataframe[0].value.iloc[0]["Cultivation"] == "PN-EXP-MG1655-001-A01-R1"


def test_saved_number_is_reused_without_requesting_a_new_number() -> None:
    app = _app()
    app.session_state["initial_registry"] = {"CultivationExperimentCode": "001"}
    app.run()
    assert not app.exception
    assert next(w for w in app.text_input if w.label == "Experiment number").value == "001"
    assert "number_queries" not in app.session_state
    app.run()
    assert next(w for w in app.text_input if w.label == "Experiment number").value == "001"
    assert "number_queries" not in app.session_state


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
    "assay_type": AssayType.GROWTH,
    "plate_custom_json": {"cultivation_registry": st.session_state.get("initial_registry", {})},
    "updated_at": "v1"
}, ({"well_id": "w1", "position": "A1", "strain": "MG1655", "replicate": 1,
     "custom_json": "{}", "medium": "MOPS"},
    {"well_id": "w2", "position": "A2", "strain": "MG1655", "replicate": 2,
     "custom_json": "{}", "medium": "MOPS"},
    {"well_id": "w3", "position": "B1", "strain": "11_J3", "replicate": 1,
     "custom_json": "{}", "medium": "MOPS"}), (), ())
class SaveService:
    def __init__(self, repository): pass
    def execute(self, actor, plate_id, version, registry, assignments):
        st.session_state["saved_registry"] = registry
        st.session_state["save_count"] = st.session_state.get("save_count", 0) + 1
        st.session_state["saved_assignments"] = assignments
        assert assignments[0].cultivation_run == ("23" if assignments[0].pattern is None else "")
        assert version == "v1"
        return snapshot
ui.SaveGrowthCultivationsService = SaveService
class Repository:
    def growth_cultivation_wells(self):
        rows = tuple({
            **snapshot.metadata, **well, "plate_id": "p", "row_index": index,
            "column_index": 0, "experiment_date": "2026-09-12",
            "created_at": "2026-09-12T00:00:00"
        } for index, well in enumerate(snapshot.wells))
        return (*rows, *st.session_state.get("replicate_rows", ()))
    def growth_cultivation_codes(self):
        st.session_state["number_queries"] = st.session_state.get("number_queries", 0) + 1
        return st.session_state.get("number_rows", ({
            "plate_id": "p", "record_type": "plate", "experiment_date": "2026-09-12",
            "created_at": "2026-09-12T00:00:00", "custom_json": {}
        },))
original_editor = st.data_editor
def select_well(frame, **kwargs):
    edited = frame.copy()
    edited["Generate"] = True
    if "Cultivation run number" in edited:
        edited["Cultivation run number"] = "23"
    return edited
st.data_editor = select_well
try:
    ui.render_growth_cultivations(
        AppContext(Repository(), Actor(UserId("u"), "test@example.invalid", Role.EDITOR)),
        PlateId("p"), GrowthRunView(snapshot, (), ())
    )
finally:
    st.data_editor = original_editor
""",
        default_timeout=30,
    )
