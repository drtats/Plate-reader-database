"""Characterization tests for the user-approved Growth workspace direction."""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

WORKSPACE_TABS = (
    "Overview & QC",
    "Metadata",
    "Layout",
    "Plotting",
    "Background history",
    "Export",
    "Activity log",
)


def test_real_growth_selector_component_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "development")

    app = AppTest.from_file("tests/ui/growth_selector_app.py", default_timeout=30).run()

    assert not app.exception
    assert {tab.label for tab in app.tabs} == {
        "96-well selection",
        "Selection list",
        "Metadata filters",
    }
    assert [item.label for item in app.expander] == [
        "Row and column shortcuts",
        "Reference plate",
    ]
    reference_field = next(item for item in app.selectbox if item.label == "Show in 96-well layout")
    assert {"Display name", "Strain", "Concentration"}.issubset(reference_field.options)
    reference_field.set_value("Strain").run()
    assert any("strain-a" in item.value for item in app.markdown)
    assert _button_labels(app).issuperset({"Select all", "Clear all", "Invert"})
    assert "Apply 96-well selection" not in _button_labels(app)
    assert "Apply selection list" not in _button_labels(app)
    assert {checkbox.label for checkbox in app.checkbox}.issuperset({"A1", "A12", "H1", "H12"})
    assert any("Grid checks are staged locally" in item.value for item in app.caption)
    assert any(item.label == "Filter fields" for item in app.multiselect)

    next(item for item in app.checkbox if item.label == "A9").set_value(True)
    _click(app, "Render selected curves")
    assert app.session_state["rendered_growth_selection"] == tuple(
        f"A{column}" for column in range(1, 10)
    )

    next(item for item in app.multiselect if item.label == "Selected wells (list)").set_value(
        ["B1"]
    ).run()
    assert app.session_state["component_growth_selection"] == ("B1",)
    assert _selected_metric(app) == "1"
    _click(app, "Render selected curves")
    assert app.session_state["rendered_growth_selection"] == ("B1",)


def test_growth_workspace_save_boundaries_and_raw_immutability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "growth-feedback.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    app = AppTest.from_file("app.py", default_timeout=30).run()

    dark_mode = next(item for item in app.toggle if item.label == "Dark mode")
    dark_mode.set_value(True).run()
    assert app.session_state["dark_mode"] is True
    assert any("plate-reader-dark-mode" in item.value for item in app.markdown)

    _radio_named(app, "Navigation").set_value("New Growth Run").run()
    assert app.session_state["growth_wizard_step"] == 1

    _click(app, "Use synthetic 24-hour demo")
    assert app.session_state["growth_wizard_step"] == 2
    assert _plate_count(database_path) == 0

    _click(app, "Validate and continue")
    assert app.session_state["growth_wizard_step"] == 3
    assert "growth_preview" in app.session_state
    assert _plate_count(database_path) == 0

    _input_named(app, "Experiment name").set_value("Characterized Growth run")
    _click(app, "Save metadata and continue")
    assert app.session_state["growth_wizard_step"] == 4
    assert app.session_state["growth_metadata"]["experiment_name"] == ("Characterized Growth run")
    assert _plate_count(database_path) == 0
    assert {tab.label for tab in app.tabs}.issuperset({"96-well plate", "Full well table"})
    assert any(item.label == "Fields in display-name order" for item in app.multiselect)
    assert any(item.label == "Apply formula to" for item in app.selectbox)
    assert any(item.label == "Display-name CSV" for item in app.get("file_uploader"))
    assert any(
        item.label == "Download display-name CSV template" for item in app.get("download_button")
    )

    _click(app, "Preview generated names")
    _click(app, "Apply generated names to staged layout")
    assert set(app.session_state["growth_layout_frame"]["Display name"]) == {"1"}

    _click(app, "Accept layout and continue")
    assert app.session_state["growth_wizard_step"] == 5
    assert len(app.session_state["growth_layout_changes"]) == 96
    assert _plate_count(database_path) == 0

    _click(app, "Commit growth run")
    assert _plate_count(database_path) == 1
    assert _display_name_count(database_path, "1") == 96
    workspace_layout_key = f"workspace_growth_layout_{app.session_state['selected_plate_id']}"
    assert set(app.session_state[workspace_layout_key]["Display name"]) == {"1"}
    workspace_labels = tuple(tab.label for tab in app.tabs if tab.label in WORKSPACE_TABS)
    assert workspace_labels == WORKSPACE_TABS
    assert {tab.label for tab in app.tabs}.issuperset({"96-well plate", "Full well table"})
    assert {tab.label for tab in app.tabs}.issuperset(
        {"96-well selection", "Selection list", "Metadata filters"}
    )
    assert any(item.label == "Reference plate" for item in app.expander)
    assert any(item.label == "Show in 96-well layout" for item in app.selectbox)
    assert any(item.label == "Time-course background correction" for item in app.expander)
    assert {item.label for item in app.selectbox}.issuperset(
        {"Heatmap channel", "Heatmap timepoint", "Heatmap values"}
    )
    assert any("Heatmap: od600" in item.value and "raw" in item.value for item in app.caption)
    assert {item.value for item in app.subheader}.issuperset({"Background history", "Activity log"})
    assert {item.label for item in app.expander}.issuperset(
        {"Technical background revision details", "Technical activity details"}
    )
    assert any("saved, versioned calculation" in item.value for item in app.markdown)
    assert any("append-only audit log" in item.value for item in app.markdown)
    assert sum(button.label == "Recompute backgrounds and QC" for button in app.button) == 1
    assert _button_labels(app).issuperset(
        {
            "Save metadata",
            "Save full layout",
            "Select all",
            "Clear all",
            "Invert",
        }
    )
    assert any(item.label == "Filter fields" for item in app.multiselect)
    assert any(item.label == "Curve colors" for item in app.selectbox)
    assert any(item.label == "Curve label format" for item in app.radio)
    assert "Save well selection" not in _button_labels(app)
    assert _selected_metric(app) == "0"

    _input_named(app, "Prefix").set_value("saved-")
    _click(app, "Preview generated names")
    confirmation = next(item for item in app.checkbox if item.label.startswith("Confirm replacing"))
    confirmation.set_value(True).run()
    _click(app, "Apply and save generated names")
    assert _display_name_count(database_path, "saved-1") == 96
    _radio_named(app, "Navigation").set_value("Growth Run Library").run()
    _radio_named(app, "Navigation").set_value("Growth Workspace").run()
    assert _display_name_count(database_path, "saved-1") == 96
    assert set(app.session_state[workspace_layout_key]["Display name"]) == {"saved-1"}

    raw_before = _growth_raw_hash(database_path)
    _input_named(app, "Experiment name").set_value("Unsaved Growth name")
    app.run()
    assert _experiment_name(database_path) == "Characterized Growth run"
    assert _growth_raw_hash(database_path) == raw_before

    label_mode = next(item for item in app.radio if item.label == "Curve label format")
    label_mode.set_value("Combine fields").run()
    combined_fields = next(
        item for item in app.multiselect if item.label == "Curve label fields in order"
    )
    combined_fields.set_value(["Display name", "Replicate"]).run()
    _input_named(app, "Label separator").set_value(" + ").run()
    next(item for item in app.multiselect if item.label == "Rows").set_value(["A"]).run()
    _click(app, "Apply row/column shortcut")
    _click(app, "Render selected curves")
    styles = app.session_state["growth_plot_styles"]
    figure = app.session_state["growth_plot"]
    csv_artifact = app.session_state["growth_plot_csv"]
    wide_csv_artifact = app.session_state["growth_plot_wide_csv"]
    assert len(figure.data) == len(styles.styles)
    assert {style.position for style in styles.styles} == set(
        app.session_state[f"growth_plot_selection_{app.session_state['selected_plate_id']}"]
    )
    assert figure.data[0].line.color == styles.styles[0].color_hex
    assert csv_artifact.row_count == sum(len(trace.x) for trace in figure.data)
    assert csv_artifact.content.startswith(b"\xef\xbb\xbf")
    wide_rows = list(
        csv.reader(io.StringIO(wide_csv_artifact.content.decode("utf-8-sig"), newline=""))
    )
    assert wide_rows[0][0] == "Time (minutes)"
    assert wide_rows[0][1:] == [style.legend_label for style in styles.styles]
    assert all(style.legend_label.startswith("saved-1") for style in styles.styles)
    csv_rows = list(
        csv.DictReader(io.StringIO(csv_artifact.content.decode("utf-8-sig"), newline=""))
    )
    assert {row["Well"] for row in csv_rows} == {style.position for style in styles.styles}
    first_style = styles.styles[0]
    first_series = sorted(
        (
            row
            for row in csv_rows
            if row["Well"] == first_style.position and row["Channel"] == first_style.channel
        ),
        key=lambda row: int(row["Time index"]),
    )
    assert [float(row["Plotted value"]) for row in first_series] == [
        float(custom[0]) for custom in figure.data[0].customdata
    ]
    assert any(
        item.label == "Download database data (long CSV)" for item in app.get("download_button")
    )
    assert any(item.label == "Download plot data (wide CSV)" for item in app.get("download_button"))
    assert any("Selected wells:" in item.value for item in app.caption)

    _input_named(app, "Experiment name").set_value("Saved Growth name")
    _click(app, "Save metadata")
    assert _experiment_name(database_path) == "Saved Growth name"
    assert _growth_raw_hash(database_path) == raw_before

    _click(app, "Save full layout")
    assert _growth_raw_hash(database_path) == raw_before

    _click(app, "Select all")
    assert _selected_metric(app) == "96"
    app.run()
    assert _selected_metric(app) == "96"
    assert _selected_well_count(database_path) == 0

    _click(app, "Clear all")
    assert _selected_metric(app) == "0"
    assert any("Select at least one well" in item.value for item in app.info)
    _click(app, "Invert")
    assert _selected_metric(app) == "96"
    assert _selected_well_count(database_path) == 0
    assert _growth_raw_hash(database_path) == raw_before


def _click(app: AppTest, label: str) -> AppTest:
    next(button for button in app.button if button.label == label).click().run()
    return app


def _input_named(app: AppTest, label: str) -> Any:
    return next(item for item in app.text_input if item.label == label)


def _radio_named(app: AppTest, label: str) -> Any:
    return next(item for item in app.radio if item.label == label)


def _button_labels(app: AppTest) -> set[str]:
    return {button.label for button in app.button}


def _selected_metric(app: AppTest) -> str:
    return str(next(metric.value for metric in app.metric if metric.label == "Selected wells"))


def _plate_count(database_path: Path) -> int:
    if not database_path.exists():
        return 0
    with sqlite3.connect(database_path) as database:
        row = database.execute("SELECT count(*) FROM plates").fetchone()
    assert row is not None
    return int(row[0])


def _experiment_name(database_path: Path) -> str:
    with sqlite3.connect(database_path) as database:
        row = database.execute("SELECT name FROM experiments").fetchone()
    assert row is not None
    return str(row[0])


def _selected_well_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as database:
        row = database.execute("SELECT sum(plot_selected) FROM wells").fetchone()
    assert row is not None
    return int(row[0])


def _display_name_count(database_path: Path, display_name: str) -> int:
    with sqlite3.connect(database_path) as database:
        row = database.execute(
            "SELECT count(*) FROM wells WHERE display_name = ?", (display_name,)
        ).fetchone()
    assert row is not None
    return int(row[0])


def _growth_raw_hash(database_path: Path) -> str:
    with sqlite3.connect(database_path) as database:
        rows = database.execute(
            "SELECT well_id, channel, time_index, elapsed_microseconds, value_raw "
            "FROM growth_measurements ORDER BY well_id, channel, time_index"
        ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()
