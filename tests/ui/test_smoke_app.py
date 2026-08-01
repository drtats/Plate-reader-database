"""Streamlit smoke tests for the Phase 0 entry point."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def test_smoke_app_renders_in_fake_cloud_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(tmp_path / "ui-smoke.sqlite"))

    app = AppTest.from_file("app.py", default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "Plate Reader Database"
    assert app.header[0].value == "Run Library"
    assert "Fake cloud mode is active" in app.info[0].value


def test_real_dual_view_plate_editor_component_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "development")

    app = AppTest.from_file("tests/ui/plate_editor_app.py", default_timeout=30).run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["96-well plate", "Full well table"]
    assert any(button.label == "Apply 96-well plate changes" for button in app.button)
    assert any(button.label == "Apply full table changes" for button in app.button)


def test_cloud_mode_stops_at_login_before_loading_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "production")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "cloud")
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    app = AppTest.from_file("app.py", default_timeout=30).run()

    assert not app.exception
    assert any("Sign in" in item.value for item in app.info)
    assert any(button.label == "Sign in" for button in app.button)
    assert not app.error


def test_growth_ui_navigation_import_edit_plot_export_and_safe_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "ui-flow.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    app = AppTest.from_file("app.py", default_timeout=30).run()

    app.radio[0].set_value("New Growth Run").run()
    click(app, "Use synthetic 24-hour demo")
    assert app.subheader[0].value == "2. Validate and preview"
    click(app, "Validate and continue")
    assert app.subheader[0].value == "3. Describe the run"
    input_named(app, "Experiment name").set_value("UI workflow experiment")
    click(app, "Save metadata and continue")
    assert app.subheader[0].value == "4. Review the 96-well layout"
    click(app, "Accept layout and continue")
    assert app.subheader[0].value == "5. Review and commit"
    click(app, "Commit growth run")

    assert not app.exception
    assert app.header[0].value == "UI workflow experiment — Plate 1"
    assert any("Run committed successfully" in item.value for item in app.success)
    with sqlite3.connect(database_path) as database:
        counts_before = database.execute(
            "SELECT (SELECT count(*) FROM growth_measurements), "
            "(SELECT count(*) FROM provenance_events), "
            "(SELECT count(*) FROM schema_migrations)"
        ).fetchone()
    assert counts_before == (13_920, 1, 1)

    input_named(app, "Experiment name").set_value("UI edited experiment")
    click(app, "Save metadata")
    assert app.header[0].value == "UI edited experiment — Plate 1"
    click(app, "Render selected curves")
    assert len(app.get("plotly_chart")) >= 2
    click(app, "Prepare portable export")
    artifact = app.session_state["portable_artifact"]
    assert artifact.content.startswith(b"SQLite format 3")
    portable_path = tmp_path / "ui-portable.plate-reader.sqlite"
    portable_path.write_bytes(artifact.content)

    app.radio[0].set_value("Import Portable Data").run()
    input_named(app, "Portable file path").set_value(str(portable_path))
    click(app, "Preview portable local path")
    assert app.subheader[0].value == "Validated portable contents"
    assert next(metric for metric in app.metric if metric.label == "ID collisions").value != "0"
    click(app, "Import portable data")
    assert any("Imported 1 plate" in item.value for item in app.success)

    app.run()
    with sqlite3.connect(database_path) as database:
        counts_after = database.execute(
            "SELECT (SELECT count(*) FROM growth_measurements), "
            "(SELECT count(*) FROM provenance_events), "
            "(SELECT count(*) FROM schema_migrations), "
            "(SELECT count(*) FROM plates)"
        ).fetchone()
    assert counts_after == (27_840, 5, 1, 2)


def test_mic_ui_import_review_edit_visualize_and_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "mic-ui.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    app = AppTest.from_file("app.py", default_timeout=30).run()

    app.radio[0].set_value("New MIC Plate").run()
    click(app, "Use synthetic MIC demo")
    assert app.subheader[0].value == "2. Validate and calculate"
    click(app, "Validate MIC plate")
    assert app.subheader[0].value == "3. Describe the MIC experiment"
    input_named(app, "MIC experiment name").set_value("UI MIC experiment")
    click(app, "Save MIC metadata and continue")
    assert app.subheader[0].value == "4. Review and optionally edit the layout"
    app.run()  # Drop stale rich-form widgets retained by Streamlit AppTest.
    click(app, "Accept MIC layout and continue")
    assert app.subheader[0].value == "5. Review and commit"
    click(app, "Commit MIC plate")

    assert not app.exception
    assert app.header[0].value == "UI MIC experiment — MIC Plate 1"
    assert any("MIC plate committed successfully" in item.value for item in app.success)
    with sqlite3.connect(database_path) as database:
        counts_before = database.execute(
            "SELECT (SELECT count(*) FROM mic_readings), "
            "(SELECT count(*) FROM mic_results), "
            "(SELECT count(*) FROM analysis_revisions), "
            "(SELECT count(*) FROM provenance_events)"
        ).fetchone()
    assert counts_before == (96, 4, 1, 1)

    click(app, "Save MIC well")
    click(app, "Compute MIC revision")
    next(item for item in app.checkbox if item.label == "MIC manually checked").set_value(True)
    click(app, "Save MIC review state")
    input_named(app, "MIC experiment name").set_value("UI MIC edited")
    next(item for item in app.number_input if item.label == "MIC threshold").set_value(0.12)
    click(app, "Save MIC metadata")
    assert app.header[0].value == "UI MIC edited — MIC Plate 1"
    click(app, "Prepare MIC portable export")
    assert app.session_state["mic_portable_artifact"].content.startswith(b"SQLite format 3")

    app.radio[0].set_value("MIC Results").run()
    assert app.header[0].value == "MIC Results"
    click(app, "Render MIC dot plot")
    assert len(app.get("plotly_chart")) == 1
    with sqlite3.connect(database_path) as database:
        final = database.execute(
            "SELECT (SELECT is_checked FROM plates), "
            "(SELECT count(*) FROM mic_readings), "
            "(SELECT count(*) FROM analysis_revisions), "
            "(SELECT count(*) FROM provenance_events)"
        ).fetchone()
    assert final == (1, 96, 4, 5)

    app.radio[0].set_value("MIC Plate Library").run()
    assert app.header[0].value == "MIC Plate Library"
    click(app, "Open MIC workspace")
    assert app.header[0].value == "UI MIC edited — MIC Plate 1"


def test_empty_mic_navigation_and_source_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(tmp_path / "empty-mic-ui.sqlite"))
    app = AppTest.from_file("app.py", default_timeout=30).run()

    app.radio[0].set_value("MIC Plate Library").run()
    assert app.header[0].value == "MIC Plate Library"
    assert any("No MIC plates" in item.value for item in app.info)
    app.radio[0].set_value("MIC Workspace").run()
    assert any("Choose a MIC plate" in item.value for item in app.info)
    app.radio[0].set_value("MIC Results").run()
    assert app.header[0].value == "MIC Results"
    assert any("No MIC result" in item.value for item in app.info)
    app.radio[0].set_value("New MIC Plate").run()
    click(app, "Use selected MIC source")
    assert any("Choose a MIC file" in item.value for item in app.caption)


def test_admin_mic_lock_and_soft_delete_ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database_path = tmp_path / "admin-mic-ui.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("PLATE_READER_DEV_ROLE", "admin")
    app = AppTest.from_file("app.py", default_timeout=30).run()
    app.radio[0].set_value("New MIC Plate").run()
    click(app, "Use synthetic MIC demo")
    click(app, "Validate MIC plate")
    input_named(app, "MIC experiment name").set_value("Admin MIC")
    click(app, "Save MIC metadata and continue")
    app.run()  # Drop stale rich-form widgets retained by Streamlit AppTest.
    click(app, "Accept MIC layout and continue")
    click(app, "Commit MIC plate")

    lock = next(item for item in app.checkbox if item.label == "Locked from deletion")
    lock.set_value(True)
    click(app, "Save MIC lock state")
    lock = next(item for item in app.checkbox if item.label == "Locked from deletion")
    assert lock.value is True
    assert next(
        button for button in app.button if button.label == "Confirm soft delete MIC plate"
    ).disabled
    lock.set_value(False)
    click(app, "Save MIC lock state")
    click(app, "Confirm soft delete MIC plate")

    assert app.header[0].value == "MIC Plate Library"
    with sqlite3.connect(database_path) as database:
        state = database.execute(
            "SELECT is_locked, deleted_at IS NOT NULL, deleted_by IS NOT NULL FROM plates"
        ).fetchone()
    assert state == (0, 1, 1)


def click(app: AppTest, label: str) -> AppTest:
    button = next(button for button in app.button if button.label == label)
    button.click().run()
    return app


def input_named(app: AppTest, label: str) -> object:
    return next(item for item in app.text_input if item.label == label)
