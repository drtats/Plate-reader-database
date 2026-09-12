"""Library batch editing loads only explicit metadata and saves on submission."""

from streamlit.testing.v1 import AppTest


def _button(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def test_library_bulk_cultivation_edits_selected_fields_after_explicit_action() -> None:
    app = _app().run()
    assert not app.exception
    assert "metadata_reads" not in app.session_state
    _button(app, "Edit cultivation metadata").click().run()
    assert not app.exception
    assert app.session_state["metadata_reads"] == 1
    assert any("Editing 2 selected run" in caption.value for caption in app.caption)
    assert app.session_state["writes"] == []
    app.multiselect[0].set_value(["Objective", "CultivationProtocol"]).run()
    assert app.session_state["metadata_reads"] == 1
    assert app.session_state["writes"] == []
    next(widget for widget in app.text_input if widget.label == "Objective").set_value(
        "Shared objective"
    )
    next(widget for widget in app.text_input if widget.label == "Cultivation protocol").set_value(
        "Protocol v2"
    )
    app.checkbox[0].uncheck()
    _button(app, "Save cultivation metadata to selected runs").click().run()
    assert not app.exception and not app.error
    assert app.session_state["writes"] == ["p1", "p2"]
    for plate_id in ("p1", "p2"):
        registry = app.session_state["db"][plate_id]["plate_custom_json"]["cultivation_registry"]
        assert registry["Objective"] == "Shared objective"
        assert registry["CultivationProtocol"] == "Protocol v2"
        assert registry["ProgramMetric"] == plate_id
    assert any("saved for 2 run" in message.value for message in app.success)
    assert "growth_library_cultivation_records" not in app.session_state
    assert "p1" not in app.session_state["run_cache"]
    assert "p2" not in app.session_state["run_cache"]
    assert "p3" in app.session_state["run_cache"]
    app.run()
    assert app.session_state["writes"] == ["p1", "p2"]


def test_library_bulk_cancel_and_search_reset_without_writes() -> None:
    app = _app().run()
    _button(app, "Edit cultivation metadata").click().run()
    _button(app, "Cancel cultivation metadata editing").click().run()
    assert not app.exception
    assert "growth_library_cultivation_records" not in app.session_state
    assert app.session_state["writes"] == []
    _button(app, "Edit cultivation metadata").click().run()
    app.multiselect[0].set_value(["Objective"]).run()
    _button(app, "Search").click().run()
    assert not app.exception
    assert "growth_library_cultivation_records" not in app.session_state
    assert not app.multiselect
    assert app.session_state["writes"] == []


def test_library_bulk_requires_selection_and_disables_viewer_action() -> None:
    app = _app().run()
    app.session_state["selected_ids"] = []
    _button(app, "Edit cultivation metadata").click().run()
    assert not app.exception and app.error
    assert "metadata_reads" not in app.session_state
    app.session_state["role"] = "viewer"
    app.run()
    assert _button(app, "Edit cultivation metadata").disabled


def test_bulk_error_retains_inputs_and_reopen_uses_fresh_metadata() -> None:
    app = _app().run()
    _button(app, "Edit cultivation metadata").click().run()
    app.multiselect[0].set_value(["Objective"]).run()
    next(widget for widget in app.text_input if widget.label == "Objective").set_value(
        "New objective"
    )
    app.checkbox[0].uncheck()
    app.session_state["db"]["p2"]["updated_at"] = "external-update"
    _button(app, "Save cultivation metadata to selected runs").click().run()
    assert not app.exception and app.error
    assert app.session_state["writes"] == []
    assert (
        next(widget for widget in app.text_input if widget.label == "Objective").value
        == "New objective"
    )
    assert "growth_library_cultivation_records" in app.session_state
    _button(app, "Edit cultivation metadata").click().run()
    assert not app.exception and not app.error
    assert app.multiselect[0].value == []
    assert (
        app.session_state["growth_library_cultivation_records"][1].updated_at == "external-update"
    )


def test_bulk_fill_empty_default_preserves_populated_values_and_noop_does_not_write() -> None:
    app = _app().run()
    _button(app, "Edit cultivation metadata").click().run()
    app.multiselect[0].set_value(["Objective"]).run()
    assert app.checkbox[0].value
    next(widget for widget in app.text_input if widget.label == "Objective").set_value(
        "New objective"
    )
    _button(app, "Save cultivation metadata to selected runs").click().run()
    assert not app.exception and not app.error
    assert app.session_state["writes"] == []
    assert any("no cultivation metadata changes" in item.value for item in app.success)
    app.run()
    assert not app.exception


def _app() -> AppTest:
    return AppTest.from_string(
        """
from contextlib import contextmanager
from copy import deepcopy
import streamlit as st
from plate_reader.application.contracts import Actor, AssayType, ExperimentId, PlateId, Role, UserId
from plate_reader.application.ports.repositories import RunSummary
from plate_reader.ui.context import AppContext
from plate_reader.ui.pages import render_run_library

if "db" not in st.session_state:
    st.session_state.db = {f"p{i}": {
        "plate_id": f"p{i}", "experiment_name": f"Experiment {i}", "plate_name": f"Plate {i}",
        "updated_at": "v1", "plate_custom_json": {"unrelated": i, "cultivation_registry": {
            "Objective": f"Old objective {i}", "ProgramMetric": f"p{i}"}}
    } for i in range(1, 4)}
    st.session_state.writes = []
    st.session_state.events = []
    st.session_state.run_cache = {"p1": "cached", "p2": "cached", "p3": "cached"}
    st.session_state.selected_ids = ["p1", "p2"]
    st.session_state.role = "editor"

class Repository:
    def user_by_email(self, email):
        return {"user_id": "u", "role": st.session_state.role, "is_active": True}
    def search_runs(self, filters):
        return tuple(RunSummary(ExperimentId(f"e{i}"),
            PlateId(row["plate_id"]), row["experiment_name"],
            row["plate_name"], AssayType.GROWTH, "2026-09-12", "Project", row["updated_at"])
            for i, row in enumerate(st.session_state.db.values()))
    def list_saved_options(self, option_type=None): return ()
    def growth_cultivation_metadata(self, plate_ids):
        st.session_state.metadata_reads = st.session_state.get("metadata_reads", 0) + 1
        return tuple(deepcopy(st.session_state.db[pid])
            for pid in plate_ids if pid in st.session_state.db)
    def load_plate(self, plate_id): raise AssertionError("Bulk metadata must not load raw data")
    @contextmanager
    def transaction(self):
        before = deepcopy(st.session_state.db)
        writes = list(st.session_state.writes)
        events = list(st.session_state.events)
        try: yield
        except BaseException:
            st.session_state.db = before
            st.session_state.writes = writes
            st.session_state.events = events
            raise
    def update_plate_metadata(self, plate_id, expected_updated_at, changes):
        row = st.session_state.db[plate_id]
        assert row["updated_at"] == expected_updated_at
        row["plate_custom_json"] = deepcopy(changes["custom_json"])
        row["updated_at"] = row["updated_at"] + "x"
        st.session_state.writes.append(plate_id)
        return row["updated_at"]
    def append_provenance(self, values):
        st.session_state.events.append(values)
        return "event"

original_editor = st.data_editor

def selected_table(frame, **kwargs):
    edited = frame.copy()
    edited["Select"] = edited.index.isin(st.session_state.selected_ids)
    return edited
st.data_editor = selected_table
try:
    render_run_library(AppContext(Repository(),
        Actor(UserId("u"), "u@example.invalid", Role(st.session_state.role))))
finally:
    st.data_editor = original_editor
""",
        default_timeout=30,
    )
