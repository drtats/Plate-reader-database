"""UI coverage for metadata-first multi-run Growth CSV export."""

from __future__ import annotations

import csv
import io
import json

from streamlit.testing.v1 import AppTest


def test_default_registry_preview_is_metadata_only_and_viewer_cannot_save() -> None:
    app = _export_page_app(legacy=False, registry=True).run()
    assert not app.exception and not app.error
    assert next(b for b in app.button if b.label == "Save IDs and prepare export").disabled
    assert next(w for w in app.selectbox if w.label == "Cultivation workflow").value == (
        "Saved experiment + condition IDs (recommended)"
    )
    assert "raw_load_calls" not in app.session_state
    assert "registry_well_calls" not in app.session_state
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert "raw_load_calls" not in app.session_state
    assert app.session_state["registry_well_calls"] == 1
    assert "registry_plate_writes" not in app.session_state
    preview = app.session_state["growth_registry_preview"]
    assert [plan.plate_number for plan in preview.plates] == ["01", "02"]
    assert [
        assignment["Cultivation"] for plan in preview.plates for assignment in plan.assignments
    ] == [
        "ST-EXP-MG1655-MP96A0101R1",
        "ST-EXP-MG1655-MP96A0101R2",
        "ST-EXP-MG1655-MP96A0102R1",
        "ST-EXP-MG1655-MP96A0201R1",
    ]
    assert preview.plates[0].registry["CultivationExperiment"] == ("ST-EXP-MG1655-MP96A[0101-0102]")
    assert preview.plates[0].assignments[0]["PreviousCultivationIDs"] == ["OLD-CULT-1"]
    assert next(b for b in app.button if b.label == "Save cultivation IDs").disabled
    assert next(b for b in app.button if b.label == "Save IDs and prepare export").disabled
    assert len(app.dataframe) >= 3


def test_preview_only_prepare_rejects_unsaved_ids_without_downloads() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not next(b for b in app.button if b.label == "Save IDs and prepare export").disabled
    next(b for b in app.button if b.label == "Prepare selected runs").click().run()
    assert not app.exception and app.error
    assert not app.get("download_button")
    assert "growth_tabular_export_bundle" not in app.session_state
    assert "registry_plate_writes" not in app.session_state


def test_search_failure_and_empty_search_show_no_prepared_download() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    app.session_state["fail_search"] = True
    next(b for b in app.button if b.label == "Search runs").click().run()
    assert not app.exception and app.error
    assert "Unable to search Growth runs" in app.error[0].value
    assert not app.get("download_button")
    app.session_state["fail_search"] = False
    app.session_state["empty_search"] = True
    next(b for b in app.button if b.label == "Search runs").click().run()
    assert not app.exception and not app.error
    assert any("No Growth runs match" in item.value for item in app.info)
    assert not app.get("download_button")
    assert not app.get("data_editor")


def test_cached_results_report_layout_column_failure() -> None:
    app = _export_page_app(legacy=False, registry=True).run()
    del app.session_state["growth_export_custom_columns"]
    app.session_state["fail_layout_columns"] = True
    app.run()
    assert not app.exception and app.error
    assert "Unable to load Growth layout columns" in app.error[0].value
    assert not app.get("download_button")


def test_editor_combined_action_saves_and_exports_once_without_refreshing_selector() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    selector_revision = app.session_state["growth_export_table_revision"]
    selector_arrow = app.session_state["export_table_arrow"]
    next(b for b in app.button if b.label == "Save IDs and prepare export").click().run()
    assert not app.exception and not app.error
    assert app.get("download_button")
    assert app.session_state["registry_plate_writes"] == 2
    assert app.session_state["registry_well_writes"] == 2
    assert app.session_state["registry_provenance_writes"] == 2
    assert app.session_state["raw_load_calls"] == 2
    assert app.session_state["search_calls"] == 1
    assert app.session_state["growth_export_table_revision"] == selector_revision
    assert app.session_state["export_table_arrow"] == selector_arrow
    app.run()
    assert not app.exception and not app.error and app.get("download_button")
    assert app.session_state["registry_plate_writes"] == 2
    assert app.session_state["raw_load_calls"] == 2
    assert next(b for b in app.button if b.label == "Save IDs and prepare export").disabled
    next(w for w in app.text_input if w.label == "Team code (optional)").set_value("NEW").run()
    assert not app.get("download_button")
    assert "growth_tabular_export_bundle" not in app.session_state


def test_failed_combined_save_never_exports_or_exposes_stale_download() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    app.session_state["fail_registry_save"] = True
    next(b for b in app.button if b.label == "Save IDs and prepare export").click().run()
    assert not app.exception and app.error
    assert "Unable to save cultivation IDs" in app.error[0].value
    assert not app.get("download_button")
    assert "growth_tabular_export_bundle" not in app.session_state
    assert "raw_load_calls" not in app.session_state
    app.run()
    assert next(b for b in app.button if b.label == "Save IDs and prepare export").disabled


def test_registry_preview_groups_notices_and_keeps_missing_strain_visible() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    store = app.session_state["registry_store"]
    for record in store.values():
        for well in record["wells"]:
            well["strain"] = "E. coli MG1655"
    store["plate-0"]["wells"][2]["strain"] = ""
    app.session_state["registry_store"] = store
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert (
        len([item for item in app.expander if item.label.startswith("Normalization details")]) == 1
    )
    notices = app.dataframe[-1].value
    assert len(notices) == 1
    assert notices.iloc[0]["Experiments"] == "Experiment 0, Experiment 1"
    assert len(app.warning) == 1
    assert "Experiment 0" in app.warning[0].value
    assert "A3" in app.warning[0].value


def test_failed_export_after_combined_save_reports_persisted_ids() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    app.session_state["fail_raw_load"] = True
    next(b for b in app.button if b.label == "Save IDs and prepare export").click().run()
    assert not app.exception and app.error
    assert "Cultivation IDs were saved" in app.error[0].value
    assert "saved IDs remain available" in app.error[0].value
    assert app.session_state["registry_plate_writes"] == 2
    assert not app.get("download_button")
    assert "growth_tabular_export_bundle" not in app.session_state


def test_resaving_unchanged_preview_does_not_write_again() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    next(b for b in app.button if b.label == "Save cultivation IDs").click().run()
    assert app.session_state["registry_plate_writes"] == 2
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    next(b for b in app.button if b.label == "Save cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert any("already saved" in item.value for item in app.success)
    assert app.session_state["registry_plate_writes"] == 2
    assert "raw_load_calls" not in app.session_state


def test_editor_save_is_explicit_once_and_viewer_exports_saved_new_ids() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    save = next(b for b in app.button if b.label == "Save cultivation IDs")
    assert not save.disabled
    assert "raw_load_calls" not in app.session_state
    selector_revision = app.session_state["growth_export_table_revision"]
    selector_arrow = app.session_state["export_table_arrow"]
    save.click().run()
    assert not app.exception and not app.error
    assert app.session_state["registry_plate_writes"] == 2
    assert app.session_state["registry_well_writes"] == 2
    assert app.session_state["registry_provenance_writes"] == 2
    assert "raw_load_calls" not in app.session_state
    assert app.session_state["search_calls"] == 1
    assert app.session_state["growth_export_table_revision"] == selector_revision
    assert app.session_state["export_table_arrow"] == selector_arrow
    app.run()
    assert not app.exception and not app.error
    assert app.session_state["registry_plate_writes"] == 2
    assert app.session_state["search_calls"] == 1
    assert app.session_state["growth_export_table_revision"] == selector_revision
    assert app.session_state["export_table_arrow"] == selector_arrow
    assert any("Selected Growth runs: 2" in item.value for item in app.caption)
    assert next(b for b in app.button if b.label == "Save cultivation IDs").disabled

    app.session_state["actor_role"] = "viewer"
    app.run()
    assert not app.exception and not app.error
    assert next(b for b in app.button if b.label == "Save cultivation IDs").disabled
    next(b for b in app.button if b.label == "Prepare selected runs").click().run()
    assert not app.exception and not app.error
    assert app.session_state["raw_load_calls"] == 2
    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert [row["Cultivation ID"] for row in rows] == [
        "ST-EXP-MG1655-MP96A0101R1",
        "ST-EXP-MG1655-MP96A0101R2",
        "ST-EXP-MG1655-MP96A0102R1",
        "ST-EXP-MG1655-MP96A0201R1",
    ]
    assert [row["Replicate"] for row in rows] == ["1", "2", "1", "1"]
    assert [row["Local cultivation ID"] for row in metadata] == [
        "EXP01-A01",
        "EXP01-A02",
        "EXP01-A03",
        "EXP02-A01",
    ]
    assert metadata[0]["Internal cultivation ID"] == metadata[0]["InternalCultivationID"]
    assert json.loads(metadata[0]["Well Metadata JSON"])["PreviousCultivationIDs"] == ["OLD-CULT-1"]
    assert app.get("download_button")
    assert app.session_state["registry_plate_writes"] == 2
    next(w for w in app.selectbox if w.label == "Cultivation workflow").select(
        "Legacy export patterns"
    ).run()
    assert not app.get("download_button")
    next(w for w in app.selectbox if w.label == "Cultivation workflow").select(
        "Saved experiment + condition IDs (recommended)"
    ).run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and not app.error
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2
    next(b for b in app.button if b.label == "Search runs").click().run()
    assert not app.exception and not app.error
    assert app.session_state["search_calls"] == 2
    assert app.session_state["export_table_arrow"] != selector_arrow


def test_registry_settings_and_selection_invalidate_preview_and_downloads() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not next(b for b in app.button if b.label == "Save cultivation IDs").disabled
    next(w for w in app.selectbox if w.label == "Concentration matching").select(
        "Exact values"
    ).run()
    assert next(b for b in app.button if b.label == "Save cultivation IDs").disabled
    assert "raw_load_calls" not in app.session_state
    next(w for w in app.selectbox if w.label == "Concentration matching").select(
        "2 decimal places (recommended)"
    ).run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    next(b for b in app.button if b.label == "Save cultivation IDs").click().run()
    app.run()
    next(b for b in app.button if b.label == "Prepare selected runs").click().run()
    assert app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2
    next(w for w in app.text_input if w.label == "Team code (optional)").set_value("OTHER").run()
    assert not app.get("download_button")
    assert "growth_tabular_export_bundle" not in app.session_state
    assert app.session_state["raw_load_calls"] == 2
    next(w for w in app.text_input if w.label == "Team code (optional)").set_value("").run()
    assert not app.get("download_button")
    next(b for b in app.button if b.label == "Prepare selected runs").click().run()
    assert app.get("download_button")
    assert app.session_state["raw_load_calls"] == 4
    app.session_state["selected_ids"] = ("plate-1",)
    app.run()
    assert not app.get("download_button")
    assert next(b for b in app.button if b.label == "Save cultivation IDs").disabled
    assert app.session_state["raw_load_calls"] == 4


def test_registry_preview_error_leaves_save_disabled_and_does_not_load_raw_data() -> None:
    app = _export_page_app(legacy=False, registry=True, role="editor").run()
    next(w for w in app.text_input if w.label == "Team code (optional)").set_value("BAD-TEAM").run()
    next(b for b in app.button if b.label == "Preview cultivation IDs").click().run()
    assert not app.exception and app.error
    assert next(b for b in app.button if b.label == "Save cultivation IDs").disabled
    assert "growth_registry_preview" not in app.session_state
    assert "raw_load_calls" not in app.session_state
    assert "registry_plate_writes" not in app.session_state


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
        "Background subtraction",
        "Background calculated",
        "Background QC flags",
        "Cultivation IDs",
        "Missing strain",
        "Experiment number",
        "Oxygen",
        "Last updated",
    )
    assert app.session_state["export_table_strains"] == ("PAO1", "PAO1")
    assert app.session_state["export_table_oxygen"] == ("aerobic", "anaerobic")

    assert any(
        item.value == "Legacy cultivation ID generation for this export" for item in app.subheader
    )
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
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert [row["Cultivation replicate"] for row in metadata] == ["1", "1"]
    assert [row["Replicate"] for row in rows] == ["1", "1"]
    assert rows[1]["Cultivation ID"].endswith("-R1")
    assert metadata[1]["Saved cultivation ID"].endswith("-R1")
    assert all(preview["Matching wells"] == 1 for preview in bundle.replicate_preview)
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


def test_legacy_prepare_failure_clears_previous_download() -> None:
    app = _export_page_app().run()
    _prepare_button(app).click().run()
    assert app.get("download_button")
    app.session_state["fail_raw_load"] = True
    _prepare_button(app).click().run()
    assert not app.exception and app.error
    assert "Unable to prepare Growth CSV export" in app.error[0].value
    assert not app.get("download_button")
    assert "growth_tabular_export_bundle" not in app.session_state


def test_repeated_export_warnings_are_collapsed_with_details_available() -> None:
    app = _export_page_app().run()
    _prepare_button(app).click().run()
    bundle = app.session_state["growth_tabular_export_bundle"]
    assert len(bundle.warnings) > 1
    assert len(app.warning) == 1
    assert any(item.label == "Export warning details" for item in app.expander)
    assert app.get("download_button")


def test_export_replicates_reset_for_subset_and_changed_options_hide_old_downloads() -> None:
    app = _export_page_app().run()

    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    assert app.session_state["raw_load_calls"] == 2
    full_rows = list(
        csv.DictReader(
            io.StringIO(
                app.session_state["growth_tabular_export_bundle"].measurements.content.decode()
            )
        )
    )
    plate_one_id = full_rows[1]["Cultivation ID"]

    app.session_state["selected_ids"] = ("plate-1",)
    app.run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert metadata[0]["Cultivation replicate"] == "1"
    assert rows[0]["Cultivation ID"] == plate_one_id
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
        if w.label == "Generate cultivation IDs and replicate numbers within each run"
    ).uncheck().run()
    assert not app.get("download_button")
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    saved_bundle = app.session_state["growth_tabular_export_bundle"]
    assert not saved_bundle.replicate_preview
    saved_rows = list(csv.DictReader(io.StringIO(saved_bundle.measurements.content.decode())))
    saved_metadata = list(csv.DictReader(io.StringIO(saved_bundle.metadata.content.decode())))
    assert saved_rows[0]["Cultivation ID"] == saved_metadata[0]["Saved cultivation ID"]
    assert saved_rows[0]["Replicate"] == "1"


def test_prepared_artifacts_from_previous_numbering_rule_are_hidden() -> None:
    app = _export_page_app().run()
    _prepare_button(app).click().run()
    assert not app.exception and app.get("download_button")
    signature = app.session_state["growth_tabular_export_signature"]
    assert signature[0] == "export_run_v2"
    app.session_state["growth_tabular_export_signature"] = signature[1:]
    app.run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2


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
        "PN-EXP-PAO1-002-A01-R1",
    ]
    assert [row["Cultivation experiment code"] for row in metadata] == ["001", "002"]
    assert [row["Replicate"] for row in rows] == ["1", "1"]
    assert [row["LocalReplicate"] for row in metadata] == ["1", "1"]
    assert [row["Saved cultivation ID"] for row in metadata] == ["", ""]
    assert [row["Replicate"] for row in metadata] == ["1", "1"]
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
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert rows[0]["Cultivation ID"] == "PN-EXP-PAO1-MP96A001R1"
    assert rows[1]["Cultivation ID"] == "PN-EXP-PAO1-MP96A002R1"
    assert metadata[1]["Saved cultivation ID"] == "PN-EXP-PAO1-MP96A002R1"


def test_replicates_number_matching_wells_within_each_run_and_reset_on_next_run() -> None:
    app = _export_page_app()
    app.session_state["multi_wells_per_run"] = True
    app.run()
    assert "raw_load_calls" not in app.session_state
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert [row["Cultivation ID"] for row in rows] == [
        "PN-EXP-PAO1-001-A01-R1",
        "PN-EXP-PAO1-001-A02-R2",
        "PN-EXP-PAO1-002-A01-R1",
        "PN-EXP-PAO1-002-A02-R2",
    ]
    assert [row["Replicate"] for row in rows] == ["1", "2", "1", "2"]
    assert [row["LocalReplicate"] for row in metadata] == ["1", "2", "1", "2"]
    assert all(preview["Matching wells"] == 2 for preview in bundle.replicate_preview)
    assert {preview["Run ID"] for preview in bundle.replicate_preview} == {
        "plate-0",
        "plate-1",
    }

    app.session_state["selected_ids"] = ("plate-1",)
    app.run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 2
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    subset = list(
        csv.DictReader(
            io.StringIO(
                app.session_state["growth_tabular_export_bundle"].measurements.content.decode()
            )
        )
    )
    assert [row["Cultivation ID"] for row in subset] == [
        "PN-EXP-PAO1-002-A01-R1",
        "PN-EXP-PAO1-002-A02-R2",
    ]


def test_concentration_matching_defaults_to_two_decimal_places_and_can_be_exact() -> None:
    app = _export_page_app()
    app.session_state["rounded_concentrations"] = True
    app.session_state["multi_wells_per_run"] = True
    app.session_state["selected_ids"] = ("plate-0",)
    app.run()
    matching = next(w for w in app.selectbox if w.label == "Concentration matching")
    assert matching.value == "2 decimal places (recommended)"
    assert "raw_load_calls" not in app.session_state

    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    bundle = app.session_state["growth_tabular_export_bundle"]
    rows = list(csv.DictReader(io.StringIO(bundle.measurements.content.decode())))
    metadata = list(csv.DictReader(io.StringIO(bundle.metadata.content.decode())))
    assert [row["Replicate"] for row in rows] == ["1", "2"]
    assert [row["Concentration"] for row in rows] == ["0.1875", "0.19"]
    assert [row["Concentration matching decimal places"] for row in metadata] == ["2", "2"]
    assert [row["Concentration"] for row in metadata] == ["0.1875", "0.19"]
    assert [row["Matching concentration"] for row in metadata] == ["0.19", "0.19"]
    assert all("Entered concentrations" in preview for preview in bundle.replicate_preview)
    assert all("Matching concentrations" in preview for preview in bundle.replicate_preview)
    assert app.session_state["raw_load_calls"] == 1

    next(w for w in app.selectbox if w.label == "Concentration matching").select(
        "Exact values"
    ).run()
    assert not app.get("download_button")
    assert app.session_state["raw_load_calls"] == 1
    _prepare_button(app).click().run()
    assert not app.exception and not app.error
    rows = list(
        csv.DictReader(
            io.StringIO(
                app.session_state["growth_tabular_export_bundle"].measurements.content.decode()
            )
        )
    )
    metadata = list(
        csv.DictReader(
            io.StringIO(app.session_state["growth_tabular_export_bundle"].metadata.content.decode())
        )
    )
    assert [row["Replicate"] for row in rows] == ["1", "1"]
    assert [row["Concentration matching decimal places"] for row in metadata] == ["", ""]
    assert [row["Matching concentration"] for row in metadata] == ["0.1875", "0.19"]


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


def _export_page_app(
    *, legacy: bool = True, registry: bool = False, role: str = "viewer"
) -> AppTest:
    app = AppTest.from_string(
        """
import streamlit as st
from contextlib import nullcontext
from streamlit.dataframe_util import convert_pandas_df_to_arrow_bytes
from uuid import UUID

from plate_reader.application.contracts import Actor, AssayType, ExperimentId, PlateId, Role, UserId
from plate_reader.application.ports.repositories import (
    ConcentrationRange,
    InoculumRange,
    PlateSnapshot,
    RunSummary,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.growth_export import render_growth_data_export


def registry_store():
    if "registry_store" not in st.session_state:
        store = {}
        for index in range(2):
            plate_id = f"plate-{index}"
            registry = {"Team_Code": "ST", "CultivationSystemCode": "MP96A"}
            metadata = {
                "assay_type": AssayType.GROWTH,
                "name": f"Experiment {index}",
                "plate_name": f"Plate {index}",
                "experiment_date": "2026-08-18",
                "created_at": f"2026-08-18T{index:02d}:00:00Z",
                "updated_at": "v1",
                "project": "SMS",
                "operator_name": "Researcher",
                "instrument": "Reader",
                "temperature": 37.0,
                "legacy_run_id": None,
                "experiment_custom_json": {},
                "plate_custom_json": {"cultivation_registry": registry},
            }
            positions = ("A1", "A2", "A3") if index == 0 else ("A1",)
            wells = []
            measurements = []
            for offset, position in enumerate(positions):
                well_id = str(UUID(int=index * 10 + offset + 1))
                wells.append({
                    "well_id": well_id,
                    "position": position,
                    "display_name": f"sample-{index}-{position}",
                    "raw_label": None,
                    "is_blank": False,
                    "background_group": "plate",
                    "plot_selected": False,
                    "notes": None,
                    "custom_json": {"Cultivation": "OLD-CULT-1"}
                    if index == 0 and offset == 0 else {},
                    "condition_custom_json": {},
                    "strain": "MG1655",
                    "medium": "MHB",
                    "replicate": (9, 7, 1)[offset],
                    "inoculum_size": None,
                    "grouping_label": None,
                    "treatment": "Ciprofloxacin",
                    "concentration": (0.1875, 0.19, 0.3)[offset],
                    "concentration_unit": "ug/mL",
                })
                measurements.append({
                    "well_id": well_id,
                    "channel": "od600",
                    "time_index": 0,
                    "elapsed_microseconds": 0,
                    "value_raw": 0.2,
                })
            store[plate_id] = {
                "metadata": metadata,
                "wells": wells,
                "measurements": measurements,
            }
        st.session_state["registry_store"] = store
    return st.session_state["registry_store"]


class Repository:
    def user_by_email(self, _email):
        return {
            "user_id": "user-1",
            "role": st.session_state.get("actor_role", "viewer"),
            "is_active": True,
        }

    def list_saved_options(self, option_type=None):
        if st.session_state.get("fail_layout_columns"):
            raise RuntimeError("simulated layout option failure")
        if option_type == "layout_column:growth":
            return ({
                "option_type": option_type,
                "value": "Oxygen",
                "created_by": "user-1",
                "created_at": "2026-08-18T10:00:00Z",
            },)
        return ()

    def search_runs(self, _filters):
        if st.session_state.get("fail_search"):
            raise RuntimeError("simulated search failure")
        st.session_state["search_calls"] = st.session_state.get("search_calls", 0) + 1
        if st.session_state.get("empty_search"):
            return ()
        return tuple(
            RunSummary(
                ExperimentId(f"experiment-{index}"),
                PlateId(f"plate-{index}"),
                f"Experiment {index}",
                f"Plate {index}",
                AssayType.GROWTH,
                "2026-08-18",
                "SMS",
                "2026-08-18T13:00:00Z"
                if st.session_state.get("registry_mode_data")
                and st.session_state.get("registry_plate_writes")
                else "2026-08-18T12:00:00Z",
                strains=("MG1655",)
                if st.session_state.get("registry_mode_data") else ("PAO1",),
                treatments=("Ciprofloxacin",),
                concentration_ranges=(ConcentrationRange(0.25, 1.0, "ug/mL"),),
                media=("MHB",),
                inoculum_ranges=(InoculumRange(1.0, 3.0, "x10^6 CFU/mL"),),
                custom_fields=(("oxygen", (("aerobic", "anaerobic")[index],)),),
            )
            for index in range(2)
        )

    def growth_cultivation_metadata(self, plate_ids):
        st.session_state["registry_metadata_calls"] = st.session_state.get(
            "registry_metadata_calls", 0
        ) + 1
        store = registry_store()
        return tuple(
            {"plate_id": plate_id, **store[str(plate_id)]["metadata"]}
            for plate_id in plate_ids if str(plate_id) in store
        )

    def growth_cultivation_wells(self):
        st.session_state["registry_well_calls"] = st.session_state.get(
            "registry_well_calls", 0
        ) + 1
        rows = []
        for plate_id, record in registry_store().items():
            metadata = record["metadata"]
            for well in record["wells"]:
                rows.append({
                    **well,
                    "plate_id": plate_id,
                    "experiment_date": metadata["experiment_date"],
                    "created_at": metadata["created_at"],
                    "deleted_at": None,
                    "plate_custom_json": metadata["plate_custom_json"],
                    "experiment_custom_json": metadata["experiment_custom_json"],
                    "temperature": metadata["temperature"],
                })
        return tuple(rows)

    def transaction(self):
        return nullcontext()

    def update_plate_metadata(self, plate_id, expected_version, changes):
        if st.session_state.get("fail_registry_save"):
            raise RuntimeError("simulated registry save failure")
        record = registry_store()[str(plate_id)]
        assert record["metadata"]["updated_at"] == expected_version
        record["metadata"]["plate_custom_json"] = changes["custom_json"]
        record["metadata"]["updated_at"] = "v2"
        st.session_state["registry_plate_writes"] = st.session_state.get(
            "registry_plate_writes", 0
        ) + 1

    def update_well_layout(self, plate_id, changes):
        wells = registry_store()[str(plate_id)]["wells"]
        for change in changes:
            well = next(w for w in wells if w["position"] == change["position"])
            well["custom_json"] = change["custom_json"]
        st.session_state["registry_well_writes"] = st.session_state.get(
            "registry_well_writes", 0
        ) + 1

    def append_provenance(self, _values):
        st.session_state["registry_provenance_writes"] = st.session_state.get(
            "registry_provenance_writes", 0
        ) + 1
        return "event-1"

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
        if st.session_state.get("fail_raw_load"):
            raise RuntimeError("simulated raw load failure")
        st.session_state["raw_load_calls"] = st.session_state.get("raw_load_calls", 0) + 1
        key = str(plate_id)
        if st.session_state.get("registry_mode_data"):
            record = registry_store()[key]
            return PlateSnapshot(
                PlateId(key),
                record["metadata"],
                tuple(record["wells"]),
                tuple(record["measurements"]),
                (),
            )
        index = key.rsplit("-", 1)[1]
        empty_cultivation = bool(st.session_state.get("empty_cultivation_metadata"))
        saved_legacy = bool(st.session_state.get("saved_legacy_pattern"))
        rounded_concentrations = bool(st.session_state.get("rounded_concentrations"))
        multi_wells = bool(st.session_state.get("multi_wells_per_run"))
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
        base_well = {
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
            "treatment": "Ciprofloxacin" if rounded_concentrations else None,
            "concentration": 0.1875 if rounded_concentrations else None,
            "concentration_unit": "ug/mL" if rounded_concentrations else None,
        }
        wells = [base_well]
        measurements = [{
            "well_id": f"well-{index}",
            "channel": "od600",
            "time_index": 0,
            "elapsed_microseconds": 0,
            "value_raw": 0.2,
        }]
        if multi_wells:
            second_saved_id = (
                f"PN-EXP-PAO1-MP96A{int(index)+1:03d}R2"
                if saved_legacy
                else f"PN-EXP-PAO1-{int(index)+1:03d}-A02-R2"
            )
            wells.append({
                **base_well,
                "well_id": f"well-{index}-2",
                "position": "A2",
                "display_name": f"sample-{index}-2",
                "custom_json": {} if empty_cultivation else {
                    **saved_well_custom, "Cultivation": second_saved_id
                },
                "replicate": 2,
                "concentration": 0.19 if rounded_concentrations else None,
            })
            measurements.append({
                "well_id": f"well-{index}-2",
                "channel": "od600",
                "time_index": 0,
                "elapsed_microseconds": 0,
                "value_raw": 0.2,
            })
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
            tuple(wells),
            tuple(measurements),
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
    st.session_state["export_table_arrow"] = convert_pandas_df_to_arrow_bytes(frame)
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
            Actor(
                UserId("user-1"),
                "viewer@example.invalid",
                Role(st.session_state.get("actor_role", "viewer")),
            ),
        )
    )
finally:
    st.data_editor = original_data_editor
""",
        default_timeout=30,
    )
    if legacy:
        app.session_state["growth_export_workflow"] = "Legacy export patterns"
    if registry:
        app.session_state["registry_mode_data"] = True
    app.session_state["actor_role"] = role
    return app
