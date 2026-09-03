"""UI coverage for metadata-first multi-run Growth CSV export."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_export_search_is_metadata_only_until_prepare_then_offers_both_files() -> None:
    app = _export_page_app().run()

    assert not app.exception
    assert app.header[0].value == "Growth Data Export"
    assert app.session_state["search_calls"] == 1
    assert "raw_load_calls" not in app.session_state
    assert any("Selected Growth runs: 2" in item.value for item in app.caption)
    assert app.session_state["export_table_columns"] == (
        "Select",
        "Experiment",
        "Plate",
        "Experiment date",
        "Project",
        "Strains",
        "Media",
        "Treatments",
        "Concentration range",
        "Inoculum size",
        "Oxygen",
        "Last updated",
    )
    assert app.session_state["export_table_strains"] == ("PAO1", "PAO1")
    assert app.session_state["export_table_oxygen"] == ("aerobic", "anaerobic")

    next(button for button in app.button if button.label == "Prepare selected runs").click().run()

    assert not app.exception
    assert app.session_state["raw_load_calls"] == 2
    bundle = app.session_state["growth_tabular_export_bundle"]
    assert bundle.measurements.row_count == 2
    assert bundle.metadata.row_count == 2
    assert {item.label for item in app.get("download_button")} == {
        "Download growth_runs.csv",
        "Download growth_runs_metadata.csv",
    }


def _export_page_app() -> AppTest:
    return AppTest.from_string(
        """
import streamlit as st

from plate_reader.application.contracts import Actor, AssayType, ExperimentId, PlateId, Role, UserId
from plate_reader.application.ports.repositories import (
    ConcentrationRange,
    InoculumRange,
    PlateSnapshot,
    RunSummary,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_export import render_growth_data_export


class Repository:
    def user_by_email(self, _email):
        return {"user_id": "user-1", "role": "viewer", "is_active": True}

    def list_saved_options(self, option_type=None):
        if option_type == "layout_column:growth":
            return ({
                "option_type": option_type,
                "value": "Oxygen",
                "created_by": "user-1",
                "created_at": "2026-08-18T10:00:00Z",
            },)
        return ()

    def search_runs(self, _filters):
        st.session_state["search_calls"] = st.session_state.get("search_calls", 0) + 1
        return tuple(
            RunSummary(
                ExperimentId(f"experiment-{index}"),
                PlateId(f"plate-{index}"),
                f"Experiment {index}",
                f"Plate {index}",
                AssayType.GROWTH,
                "2026-08-18",
                "SMS",
                "2026-08-18T12:00:00Z",
                strains=("PAO1",),
                treatments=("Ciprofloxacin",),
                concentration_ranges=(ConcentrationRange(0.25, 1.0, "ug/mL"),),
                media=("MHB",),
                inoculum_ranges=(InoculumRange(1.0, 3.0, "x10^6 CFU/mL"),),
                custom_fields=(("oxygen", (("aerobic", "anaerobic")[index],)),),
            )
            for index in range(2)
        )

    def load_plate(self, plate_id):
        st.session_state["raw_load_calls"] = st.session_state.get("raw_load_calls", 0) + 1
        key = str(plate_id)
        index = key.rsplit("-", 1)[1]
        return PlateSnapshot(
            PlateId(key),
            {
                "assay_type": AssayType.GROWTH,
                "name": f"Experiment {index}",
                "plate_name": f"Plate {index}",
                "experiment_date": "2026-08-18",
                "project": "SMS",
                "operator_name": "Researcher",
                "instrument": "Reader",
                "temperature": 37.0,
                "legacy_run_id": None,
                "experiment_custom_json": "{}",
                "plate_custom_json": "{}",
            },
            ({
                "well_id": f"well-{index}",
                "position": "A1",
                "display_name": f"sample-{index}",
                "raw_label": None,
                "is_blank": False,
                "background_group": "plate",
                "plot_selected": False,
                "notes": None,
                "custom_json": "{}",
                "condition_custom_json": "{}",
                "strain": "PAO1",
                "medium": "MHB",
                "replicate": 1,
                "inoculum_size": None,
                "grouping_label": None,
                "treatment": None,
                "concentration": None,
                "concentration_unit": None,
            },),
            ({
                "well_id": f"well-{index}",
                "channel": "od600",
                "time_index": 0,
                "elapsed_microseconds": 0,
                "value_raw": 0.2,
            },),
            (),
        )

    def growth_backgrounds(self, _revision_id):
        return ()

    def provenance_for_plate(self, _plate_id):
        return ()


original_data_editor = st.data_editor

def select_all(frame, **_kwargs):
    st.session_state["export_table_columns"] = tuple(frame.columns)
    st.session_state["export_table_strains"] = tuple(frame["Strains"])
    st.session_state["export_table_oxygen"] = tuple(frame["Oxygen"])
    selected = frame.copy()
    selected["Select"] = True
    return selected

st.data_editor = select_all
try:
    render_growth_data_export(
        AppContext(
            Repository(),
            Actor(UserId("user-1"), "viewer@example.invalid", Role.VIEWER),
        )
    )
finally:
    st.data_editor = original_data_editor
""",
        default_timeout=30,
    )
