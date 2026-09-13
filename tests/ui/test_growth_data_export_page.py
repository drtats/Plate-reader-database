"""UI coverage for metadata-first multi-run Growth CSV export."""

from __future__ import annotations

import csv
import io

from streamlit.testing.v1 import AppTest


def test_export_search_is_metadata_only_until_prepare_then_offers_both_files() -> None:
    app = _export_page_app().run()

    assert not app.exception
    assert app.header[0].value == "Growth Data Export"
    assert app.dataframe[0].proto.form_id == ""
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

    assert any(item.value == "Cultivation ID generation" for item in app.subheader)
    assert (
        next(widget for widget in app.selectbox if widget.label == "Cultivation ID pattern").value
        == "Experiment number + well (recommended)"
    )
    _prepare_button(app).click().run()

    assert not app.exception
    assert app.session_state["raw_load_calls"] == 2
    bundle = app.session_state["growth_tabular_export_bundle"]
    assert bundle.measurements.row_count == 2
    assert bundle.metadata.row_count == 2
    assert len(bundle.replicate_preview) == 2
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert [row["Cultivation replicate"] for row in rows] == ["1", "2"]
    assert [row["Replicate"] for row in rows] == ["1", "2"]
    assert rows[1]["Cultivation ID"].endswith("-R2")
    assert rows[1]["Saved cultivation ID"].endswith("-R1")
    download_buttons = app.get("download_button")
    assert {item.label for item in download_buttons} == {
        "Download growth_runs.csv",
        "Download growth_runs_metadata.csv",
    }
    assert all(item.proto.ignore_rerun for item in download_buttons)
    assert any(
        item.proto.id.endswith("-growth-tabular-measurements-download") for item in download_buttons
    )
    assert any(
        item.proto.id.endswith("-growth-tabular-metadata-download") for item in download_buttons
    )


def test_export_replicates_reset_for_subset_and_changed_options_hide_old_downloads() -> None:
    app = _export_page_app().run()

    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    assert app.session_state["raw_load_calls"] == 2

    app.session_state["selected_ids"] = ("plate-1",)
    app.run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert rows[0]["Cultivation replicate"] == "1"
    assert app.session_state["raw_load_calls"] == 3

    next(w for w in app.text_input if w.label.startswith("Additional condition fields")).set_value(
        "oxygen"
    ).run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 3
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    assert app.session_state["growth_tabular_export_bundle"].effective_condition_fields == (
        "oxygen",
    )

    next(
        w
        for w in app.checkbox
        if w.label == "Generate cultivation IDs and cumulative replicate numbers"
    ).uncheck().run()
    assert not app.get("download_button")
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    assert not app.session_state["growth_tabular_export_bundle"].replicate_preview


def test_generation_from_empty_saved_metadata_uses_entered_team_and_chronological_codes() -> None:
    app = _export_page_app()
    app.session_state["empty_cultivation_metadata"] = True
    app.run()
    assert not app.exception
    assert "raw_load_calls" not in app.session_state
    next(w for w in app.text_input if w.label == "Team code for this export (optional)").set_value(
        "PN"
    ).run()
    assert "raw_load_calls" not in app.session_state
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    assert app.session_state["raw_load_calls"] == 2

    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert [row["Cultivation ID"] for row in rows] == [
        "PN-EXP-PAO1-001-A01-R1",
        "PN-EXP-PAO1-002-A01-R2",
    ]
    assert [row["Cultivation experiment code"] for row in rows] == ["001", "002"]
    assert [row["Replicate"] for row in rows] == ["1", "2"]
    assert [row["LocalReplicate"] for row in metadata] == ["1", "1"]
    assert [row["Saved cultivation ID"] for row in rows] == ["", ""]
    assert [row["Replicate"] for row in metadata] == ["1", "2"]
    assert any(
        item.value == "Cultivation IDs and replicates for this export" for item in app.subheader
    )


def test_pattern_and_component_changes_invalidate_prepared_artifacts() -> None:
    app = _export_page_app().run()
    _prepare_button(app).click().run()
    assert not app.exception and app.get("download_button")

    choice = next(w for w in app.selectbox if w.label == "Cultivation ID pattern")
    choice.select("Original laboratory format").run()
    assert not app.get("download_button")
    next(
        w for w in app.text_input if w.label == "Cultivation system code for this export (optional)"
    ).set_value("MP96A").run()
    next(w for w in app.text_input if w.label == "Team code for this export (optional)").set_value(
        "PN"
    ).run()
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    assert app.get("download_button")
    rows = list(
        csv.DictReader(
            io.StringIO(
                app.session_state["growth_tabular_export_bundle"].measurements.content.decode()
            )
        )
    )
    assert rows[0]["Cultivation ID"] == "PN-EXP-PAO1-MP96A001R1"

    next(
        w for w in app.text_input if w.label == "Cultivation system code for this export (optional)"
    ).set_value("MP96B").run()
    assert not app.get("download_button")
    _prepare_button(app).click().run()
    assert not app.exception and not app.error

    next(w for w in app.text_input if w.label == "Team code for this export (optional)").set_value(
        "NEW"
    ).run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 6
    next(w for w in app.selectbox if w.label == "Cultivation ID pattern").select(
        "Custom pattern"
    ).run()
    assert not app.get("download_button")
    next(w for w in app.text_input if w.label == "Custom cultivation ID pattern").set_value(
        "{team}-{strain}-{experiment}-{well}-R{replicate}"
    ).run()
    assert not app.get("download_button")
    _prepare_button(app).click().run()
    assert not app.exception and not app.error and app.get("download_button")
    next(w for w in app.text_input if w.label == "Custom cultivation ID pattern").set_value(
        "{team}-EXP-{strain}-{experiment}-{well}-R{replicate}"
    ).run()
    assert not app.get("download_button")


def test_saved_pattern_mode_preserves_saved_pattern_choice() -> None:
    app = _export_page_app()
    app.session_state["saved_legacy_pattern"] = True
    app.run()
    next(w for w in app.selectbox if w.label == "Cultivation ID pattern").select(
        "Use saved patterns"
    ).run()
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    assert rows[0]["Cultivation ID"] == "PN-EXP-PAO1-MP96A001R1"
    assert rows[1]["Cultivation ID"] == "PN-EXP-PAO1-MP96A002R2"
    assert rows[1]["Saved cultivation ID"] == "PN-EXP-PAO1-MP96A002R1"


def _prepare_button(app: AppTest):
    return next(
        button
        for button in app.button
        if button.label
        in (
            "Generate cultivation IDs and prepare export",
            "Prepare selected runs",
        )
    )


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

    def growth_cultivation_codes(self):
        st.session_state["cultivation_code_queries"] = st.session_state.get(
            "cultivation_code_queries", 0
        ) + 1
        return tuple(
            {
                "plate_id": f"plate-{index}",
                "record_type": "plate",
                "experiment_date": "2026-08-18",
                "created_at": f"2026-08-18T{index:02d}:00:00Z",
                "custom_json": {}
                if st.session_state.get("empty_cultivation_metadata")
                else {"CultivationExperimentCode": f"{index+1:03d}"},
            }
            for index in range(2)
        )

    def load_plate(self, plate_id):
        st.session_state["raw_load_calls"] = st.session_state.get("raw_load_calls", 0) + 1
        key = str(plate_id)
        index = key.rsplit("-", 1)[1]
        empty_cultivation = bool(st.session_state.get("empty_cultivation_metadata"))
        saved_legacy = bool(st.session_state.get("saved_legacy_pattern"))
        saved_pattern = (
            "{team}-EXP-{strain}-{system}{run}R{replicate}"
            if saved_legacy
            else "{team}-EXP-{strain}-{experiment}-{well}-R{replicate}"
        )
        saved_id = (
            f"PN-EXP-PAO1-MP96A{int(index)+1:03d}R1"
            if saved_legacy
            else f"PN-EXP-PAO1-{int(index)+1:03d}-A01-R1"
        )
        saved_registry = {
            "Team_Code": "PN",
            "CultivationExperimentCode": f"{int(index)+1:03d}",
            "CultivationIDPattern": saved_pattern,
        }
        if saved_legacy:
            saved_registry["CultivationSystemCode"] = "MP96A"
            saved_registry["CultivationRun"] = f"{int(index)+1:03d}"
        saved_well_custom = {"Cultivation": saved_id, **saved_registry}
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
                "plate_custom_json": {
                    "cultivation_registry": {} if empty_cultivation else saved_registry
                },
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
                "custom_json": {} if empty_cultivation else saved_well_custom,
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
    original_data_editor(frame, **_kwargs)
    selected = frame.copy()
    ids = st.session_state.get("selected_ids", tuple(map(str, selected.index)))
    selected["Select"] = [str(plate_id) in ids for plate_id in selected.index]
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
