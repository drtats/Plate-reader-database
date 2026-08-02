"""Streamlit smoke tests for the Phase 0 entry point."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.ui import context as context_module


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
    assert app.radio[0].options == [
        "Growth Run Library",
        "New Growth Run",
        "Growth Workspace",
        "Import Portable Data",
    ]


def test_real_dual_view_plate_editor_component_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "development")

    app = AppTest.from_file("tests/ui/plate_editor_app.py", default_timeout=30).run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["96-well plate", "Full well table"]
    assert any(button.label == "Apply 96-well plate changes" for button in app.button)
    assert any(button.label == "Apply full table changes" for button in app.button)
    next(item for item in app.selectbox if item.label == "Fill parameter").set_value("Strain").run()
    fill_value = next(item for item in app.selectbox if item.label == "Fill value")
    assert "Saved strain" in fill_value.options


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


def test_hosted_cloud_mode_uses_fixed_identity_without_oidc_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    connection = connect_database(
        DatabaseConfig(
            tmp_path / "hosted-cloud-ui.sqlite",
            DatabaseBackend.FAKE_CLOUD,
            root / "migrations",
        )
    )
    repository = SqlPlateReaderRepository(connection)
    with repository.transaction():
        repository.upsert_user(
            {
                "email": "owner@example.com",
                "display_name": "owner",
                "role": "admin",
                "is_active": True,
            }
        )
    monkeypatch.setattr(
        context_module, "_cached_cloud_repository", lambda *args, **kwargs: repository
    )
    monkeypatch.setenv("PLATE_READER_ENV", "production")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "cloud")
    monkeypatch.setenv("PLATE_READER_CLOUD_IDENTITY_MODE", "hosted")
    monkeypatch.setenv("PLATE_READER_HOSTED_USER_EMAIL", "owner@example.com")
    monkeypatch.setenv("PLATE_READER_HOSTED_USER_ROLE", "admin")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://database.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")

    app = AppTest.from_file("app.py", default_timeout=30).run()

    assert not app.exception
    assert not app.error
    assert [metric.value for metric in app.metric[:3]] == [
        "0.1.0",
        "production",
        "cloud",
    ]
    assert any("Hosted audit identity: owner@example.com" in item.value for item in app.caption)
    assert not any(button.label == "Sign in" for button in app.button)
    connection.close()


def test_growth_ui_navigation_import_edit_plot_export_and_safe_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "ui-flow.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    app = AppTest.from_file("app.py", default_timeout=30).run()

    navigation_radio(app).set_value("New Growth Run").run()
    click(app, "Use synthetic 24-hour demo")
    assert app.subheader[0].value == "2. Validate and preview"
    click(app, "Validate and continue")
    assert app.subheader[0].value == "3. Describe the run"
    input_named(app, "Experiment name").set_value("UI workflow experiment")
    click(app, "Save metadata and continue")
    assert app.subheader[0].value == "4. Review the 96-well layout"
    assert any(button.label == "Apply selected template" for button in app.button)
    click(app, "Accept layout and continue")
    assert app.subheader[0].value == "5. Review and commit"
    click(app, "Commit growth run")

    assert not app.exception
    assert app.header[0].value == "UI workflow experiment — Plate 1"
    assert any("Run committed successfully" in item.value for item in app.success)
    assert any(button.label == "Render 96-well curve overview" for button in app.button)
    assert any(button.label == "Copy and save background groups" for button in app.button)
    assert any(button.label == "Recompute backgrounds and QC" for button in app.button)
    with sqlite3.connect(database_path) as database:
        counts_before = database.execute(
            "SELECT (SELECT count(*) FROM growth_series_chunks), "
            "(SELECT count(*) FROM provenance_events), "
            "(SELECT count(*) FROM schema_migrations)"
        ).fetchone()
    assert counts_before == (1, 1, 2)

    input_named(app, "Experiment name").set_value("UI edited experiment")
    input_named(app, "Project").set_value("UI project")
    input_named(app, "Tags (comma separated)").set_value("growth, ui")
    input_named(app, "User").set_value("UI researcher")
    input_named(app, "Instrument").set_value("UI reader")
    input_named(app, "Temperature unit").set_value("C")
    input_named(app, "Measurement type").set_value("OD600")
    input_named(app, "Channel").set_value("od600")
    next(item for item in app.text_area if item.label == "Run notes").set_value(
        "rich metadata retained"
    )
    next(item for item in app.number_input if item.label == "Temperature").set_value(35.5)
    next(
        item for item in app.number_input if item.label == "Global subtraction (legacy override)"
    ).set_value(0.012)
    click(app, "Save metadata")
    assert app.header[0].value == "UI edited experiment — Plate 1"
    with sqlite3.connect(database_path) as database:
        rich_metadata = database.execute(
            "SELECT e.project, e.operator_name, p.instrument, p.temperature, "
            "p.manual_subtraction, p.channel, p.custom_json FROM experiments e "
            "JOIN plates p ON p.experiment_id = e.experiment_id"
        ).fetchone()
        tags = database.execute(
            "SELECT group_concat(tag, ',') FROM "
            "(SELECT tag FROM experiment_tags ORDER BY tag COLLATE NOCASE)"
        ).fetchone()
    assert rich_metadata == (
        "UI project",
        "UI researcher",
        "UI reader",
        35.5,
        0.012,
        "od600",
        '{"measurement_type":"OD600"}',
    )
    assert tags == ("growth,ui",)
    assert {tab.label for tab in app.tabs}.issuperset({"96-well plate", "Full well table"})
    click(app, "Save full layout")
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT count(*) FROM growth_series_chunks").fetchone() == (1,)
    assert next(item for item in app.number_input if item.label == "X maximum").value == 1_400.0
    assert next(item for item in app.number_input if item.label == "Y minimum").value == 0.001
    assert next(item for item in app.number_input if item.label == "Y maximum").value == 1.5
    assert next(item for item in app.checkbox if item.label == "Symmetric log scale").value is True
    click(app, "Save well selection")
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT sum(plot_selected) FROM wells").fetchone() == (8,)
    click(app, "Render selected curves")
    assert len(app.get("plotly_chart")) >= 2
    assert app.session_state["growth_plot_pdf"].content.startswith(b"%PDF-1.4")
    click(app, "Prepare portable export")
    artifact = app.session_state["portable_artifact"]
    assert artifact.content.startswith(b"SQLite format 3")
    portable_path = tmp_path / "ui-portable.plate-reader.sqlite"
    portable_path.write_bytes(artifact.content)

    navigation_radio(app).set_value("Import Portable Data").run()
    input_named(app, "Portable file path").set_value(str(portable_path))
    click(app, "Preview portable local path")
    assert app.subheader[0].value == "Validated portable contents"
    assert next(metric for metric in app.metric if metric.label == "ID collisions").value != "0"
    click(app, "Import portable data")
    assert any("Imported 1 plate" in item.value for item in app.success)

    app.run()
    with sqlite3.connect(database_path) as database:
        counts_after = database.execute(
            "SELECT (SELECT count(*) FROM growth_series_chunks), "
            "(SELECT count(*) FROM provenance_events), "
            "(SELECT count(*) FROM schema_migrations), "
            "(SELECT count(*) FROM plates)"
        ).fetchone()
    assert counts_after == (2, 9, 2, 2)


def test_mic_ui_import_review_edit_visualize_and_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "mic-ui.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("PLATE_READER_TEST_ENABLE_MIC_UI", "1")
    app = AppTest.from_file("app.py", default_timeout=30).run()

    navigation_radio(app).set_value("New MIC Plate").run()
    click(app, "Use synthetic MIC demo")
    assert app.subheader[0].value == "2. Validate and calculate"
    click(app, "Validate MIC plate")
    assert app.subheader[0].value == "3. Describe the MIC experiment"
    input_named(app, "MIC experiment name").set_value("UI MIC experiment")
    click(app, "Save MIC metadata and continue")
    assert app.subheader[0].value == "4. Review and optionally edit the layout"
    app.run()  # Drop stale rich-form widgets retained by Streamlit AppTest.
    assert any(button.label == "Apply selected template" for button in app.button)
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
        raw_before = database.execute(
            "SELECT well_id, value_raw FROM mic_readings ORDER BY well_id"
        ).fetchall()
    assert counts_before == (96, 4, 1, 1)

    assert {tab.label for tab in app.tabs}.issuperset({"96-well plate", "Full well table"})
    click(app, "Save full MIC layout")
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT count(*) FROM mic_readings").fetchone() == (96,)
        assert (
            database.execute(
                "SELECT well_id, value_raw FROM mic_readings ORDER BY well_id"
            ).fetchall()
            == raw_before
        )
    click(app, "Compute MIC revision")
    next(item for item in app.checkbox if item.label == "MIC manually checked").set_value(True)
    click(app, "Save MIC review state")
    input_named(app, "MIC experiment name").set_value("UI MIC edited")
    input_named(app, "MIC project").set_value("MIC UI project")
    input_named(app, "MIC tags (comma separated)").set_value("mic, ui")
    input_named(app, "MIC person").set_value("MIC researcher")
    input_named(app, "MIC reader").set_value("Synergy H1")
    input_named(app, "MIC instrument").set_value("Synergy H1")
    next(item for item in app.number_input if item.label == "MIC incubation time (hrs)").set_value(
        20.0
    )
    next(item for item in app.number_input if item.label == "MIC inoculum OD").set_value(0.01)
    next(item for item in app.number_input if item.label == "MIC harvest OD").set_value(0.5)
    next(item for item in app.number_input if item.label == "MIC doubling time (min)").set_value(
        32.0
    )
    next(item for item in app.number_input if item.label == "MIC threshold").set_value(0.12)
    click(app, "Save MIC metadata")
    assert app.header[0].value == "UI MIC edited — MIC Plate 1"
    with sqlite3.connect(database_path) as database:
        metadata = database.execute(
            "SELECT e.project, e.operator_name, e.reader, e.incubation_time_hours, "
            "e.inoculum_od, e.harvest_od, e.doubling_time_minutes, p.instrument "
            "FROM experiments e JOIN plates p ON p.experiment_id = e.experiment_id"
        ).fetchone()
        tags = database.execute(
            "SELECT group_concat(tag, ',') FROM "
            "(SELECT tag FROM experiment_tags ORDER BY tag COLLATE NOCASE)"
        ).fetchone()
    assert metadata == (
        "MIC UI project",
        "MIC researcher",
        "Synergy H1",
        20.0,
        0.01,
        0.5,
        32.0,
        "Synergy H1",
    )
    assert tags == ("mic,ui",)
    click(app, "Prepare MIC portable export")
    assert app.session_state["mic_portable_artifact"].content.startswith(b"SQLite format 3")

    navigation_radio(app).set_value("MIC Results").run()
    assert app.header[0].value == "MIC Results"
    result_columns = next(item for item in app.multiselect if item.label == "Columns to display")
    assert {"Date", "Plate", "Strain", "Antibiotic / treatment", "MIC value"}.issubset(
        set(result_columns.options)
    )
    assert any(item.label == "Group MIC plot by" for item in app.multiselect)
    assert any(item.label == "Color MIC plot by" for item in app.selectbox)
    assert any(item.label == "Shape MIC plot by" for item in app.selectbox)
    assert next(item for item in app.checkbox if item.label == "Logarithmic MIC axis").value is True
    assert any("MIC Plate 1" in item.value for item in app.markdown)
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

    navigation_radio(app).set_value("MIC Plate Library").run()
    assert app.header[0].value == "MIC Plate Library"
    click(app, "Open MIC workspace")
    assert app.header[0].value == "UI MIC edited — MIC Plate 1"


def test_empty_mic_navigation_and_source_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(tmp_path / "empty-mic-ui.sqlite"))
    monkeypatch.setenv("PLATE_READER_TEST_ENABLE_MIC_UI", "1")
    app = AppTest.from_file("app.py", default_timeout=30).run()

    navigation_radio(app).set_value("MIC Plate Library").run()
    assert app.header[0].value == "MIC Plate Library"
    assert any("No MIC plates" in item.value for item in app.info)
    navigation_radio(app).set_value("MIC Workspace").run()
    assert any("Choose a MIC plate" in item.value for item in app.info)
    navigation_radio(app).set_value("MIC Results").run()
    assert app.header[0].value == "MIC Results"
    assert any("No MIC result" in item.value for item in app.info)
    navigation_radio(app).set_value("New MIC Plate").run()
    click(app, "Use selected MIC source")
    assert any("Choose a MIC file" in item.value for item in app.caption)


def test_admin_mic_lock_and_soft_delete_ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database_path = tmp_path / "admin-mic-ui.sqlite"
    monkeypatch.setenv("PLATE_READER_ENV", "test")
    monkeypatch.setenv("PLATE_READER_STORAGE_MODE", "fake-cloud")
    monkeypatch.setenv("PLATE_READER_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("PLATE_READER_DEV_ROLE", "admin")
    monkeypatch.setenv("PLATE_READER_TEST_ENABLE_MIC_UI", "1")
    app = AppTest.from_file("app.py", default_timeout=30).run()
    navigation_radio(app).set_value("New MIC Plate").run()
    click(app, "Use synthetic MIC demo")
    click(app, "Validate MIC plate")
    input_named(app, "MIC experiment name").set_value("Admin MIC")
    click(app, "Save MIC metadata and continue")
    app.run()  # Drop stale rich-form widgets retained by Streamlit AppTest.
    input_named(app, "New template name").set_value("Admin MIC template")
    click(app, "Save current layout as new template")
    with sqlite3.connect(database_path) as database:
        template_id, template_name, assay_type, layout_json = database.execute(
            "SELECT template_id, template_name, assay_type, layout_json FROM plate_templates"
        ).fetchone()
    layout = json.loads(layout_json)
    assert (template_name, assay_type, len(layout)) == ("Admin MIC template", "mic", 96)
    assert all("value_raw" not in row for row in layout)
    next(item for item in app.selectbox if item.label == "Saved template").set_value(
        template_id
    ).run()
    click(app, "Apply selected template")
    assert any("Applied template: Admin MIC template" in item.value for item in app.success)
    next(item for item in app.selectbox if item.label == "Suggestion value").set_value(
        "strain_normal"
    )
    click(app, "Save fill suggestion")
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT option_type, value FROM saved_options").fetchone() == (
            "strain",
            "strain_normal",
        )
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


def navigation_radio(app: AppTest) -> object:
    return next(item for item in app.radio if item.label == "Navigation")
