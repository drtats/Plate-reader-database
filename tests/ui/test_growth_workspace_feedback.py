"""Characterization tests for the user-approved Growth workspace direction."""

from __future__ import annotations

import hashlib
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
    "Revisions",
    "Export",
    "Provenance",
)


def test_real_growth_selector_component_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "development")

    app = AppTest.from_file("tests/ui/growth_selector_app.py", default_timeout=30).run()

    assert not app.exception
    assert {tab.label for tab in app.tabs} == {
        "Reference plate",
        "96-well selection",
        "Selection list",
        "Metadata filters",
    }
    assert _button_labels(app).issuperset(
        {"Select all", "Clear all", "Invert", "Apply 96-well selection", "Apply selection list"}
    )
    assert any(item.label == "Filter fields" for item in app.multiselect)


def test_growth_workspace_save_boundaries_and_raw_immutability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "growth-feedback.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    app = AppTest.from_file("app.py", default_timeout=30).run()

    app.radio[0].set_value("New Growth Run").run()
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
        {"Reference plate", "96-well selection", "Selection list", "Metadata filters"}
    )
    assert _button_labels(app).issuperset(
        {
            "Save metadata",
            "Save full layout",
            "Save well selection",
            "Select all",
            "Clear all",
            "Invert",
        }
    )
    assert any(item.label == "Filter fields" for item in app.multiselect)

    raw_before = _growth_raw_hash(database_path)
    _input_named(app, "Experiment name").set_value("Unsaved Growth name")
    app.run()
    assert _experiment_name(database_path) == "Characterized Growth run"
    assert _growth_raw_hash(database_path) == raw_before

    _input_named(app, "Experiment name").set_value("Saved Growth name")
    _click(app, "Save metadata")
    assert _experiment_name(database_path) == "Saved Growth name"
    assert _growth_raw_hash(database_path) == raw_before

    _click(app, "Save full layout")
    assert _growth_raw_hash(database_path) == raw_before

    _click(app, "Save well selection")
    assert _selected_well_count(database_path) == 8
    assert _growth_raw_hash(database_path) == raw_before

    _click(app, "Select all")
    assert _selected_metric(app) == "96"
    app.run()
    assert _selected_metric(app) == "96"
    assert _selected_well_count(database_path) == 8

    _click(app, "Clear all")
    assert _selected_metric(app) == "0"
    _click(app, "Invert")
    assert _selected_metric(app) == "96"
    assert _selected_well_count(database_path) == 8
    assert _growth_raw_hash(database_path) == raw_before


def _click(app: AppTest, label: str) -> AppTest:
    next(button for button in app.button if button.label == label).click().run()
    return app


def _input_named(app: AppTest, label: str) -> Any:
    return next(item for item in app.text_input if item.label == label)


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
