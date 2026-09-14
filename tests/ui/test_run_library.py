"""Focused tests for the metadata-only Growth Run Library table."""

from __future__ import annotations

from types import SimpleNamespace

from plate_reader.application.ports.repositories import GrowthRunReadiness
from plate_reader.ui.run_summary_table import run_summary_rows, run_summary_table


def test_run_library_rows_render_blank_metadata_as_em_dash() -> None:
    run = SimpleNamespace(
        plate_id="plate-1",
        experiment_name="Experiment",
        plate_name="Plate 1",
        experiment_date="2026-08-17",
        project=None,
        strains=(),
        media=(),
        treatments=(),
        concentration_ranges=(),
        inoculum_ranges=(),
        updated_at="2026-08-17T12:00:00Z",
    )

    row = run_summary_rows((run,))[0]

    assert row == {
        "plate_id": "plate-1",
        "Select": False,
        "Experiment": "Experiment",
        "Plate": "Plate 1",
        "Experiment date": "2026-08-17",
        "Project": "—",
        "Strains": "—",
        "Media": "—",
        "Treatments": "—",
        "Concentration range": "—",
        "Inoculum size": "—",
        "Background subtraction": "—",
        "Background calculated": "—",
        "Background QC flags": "—",
        "Cultivation IDs": "—",
        "Missing strain": "—",
        "Experiment number": "—",
        "Last updated": "2026-08-17T12:00:00Z",
    }


def test_run_library_table_keeps_plate_id_as_hidden_stable_index() -> None:
    concentration_ranges = (
        SimpleNamespace(minimum=0.25, maximum=1.0, unit="µg/mL"),
        SimpleNamespace(minimum=2.0, maximum=2.0, unit="mM"),
        SimpleNamespace(minimum=0.5, maximum=0.5, unit=None),
    )
    inoculum_ranges = (
        SimpleNamespace(minimum=1.0, maximum=3.0, unit="x10^6 CFU/mL"),
        SimpleNamespace(minimum=0.05, maximum=0.05, unit=None),
    )
    run = SimpleNamespace(
        plate_id="plate-1",
        experiment_name="Experiment",
        plate_name="Plate 1",
        experiment_date="2026-08-17",
        project="Project A",
        strains=("PAO1",),
        media=("MHB", "LB"),
        treatments=("Ciprofloxacin",),
        concentration_ranges=concentration_ranges,
        inoculum_ranges=inoculum_ranges,
        custom_fields=(("oxygen", ("anaerobic", "aerobic")),),
        updated_at="2026-08-17T12:00:00Z",
    )

    table = run_summary_table((run,), ("Oxygen", "Vessel"))

    assert list(table.index) == ["plate-1"]
    assert "plate_id" not in table.columns
    assert table.loc["plate-1", "Concentration range"] == (
        "0.25\N{EN DASH}1 µg/mL, 2 mM, 0.5 (unit not set)"
    )
    assert table.loc["plate-1", "Strains"] == "PAO1"
    assert table.loc["plate-1", "Media"] == "MHB, LB"
    assert table.loc["plate-1", "Inoculum size"] == (
        "1\N{EN DASH}3 x10^6 CFU/mL, 0.05 (unit not set)"
    )
    assert table.loc["plate-1", "Oxygen"] == "anaerobic, aerobic"
    assert table.loc["plate-1", "Vessel"] == "—"


def test_run_library_rows_show_saved_readiness_without_inventing_experiment_number() -> None:
    run = SimpleNamespace(
        plate_id="plate-2",
        experiment_name="Experiment",
        plate_name="Plate",
        experiment_date="2026-09-14",
        project=None,
        strains=("PAO1",),
        media=(),
        treatments=(),
        concentration_ranges=(),
        inoculum_ranges=(),
        updated_at="2026-09-14T12:00:00Z",
        growth_readiness=GrowthRunReadiness(
            background_status="Calculated (verify)",
            background_calculated_at="2026-09-13T09:00:00Z",
            background_qc_flags=0,
            cultivation_status="Partial",
            cultivation_saved=3,
            cultivation_total=5,
            missing_strain=2,
        ),
    )

    row = run_summary_rows((run,))[0]

    assert row["Background subtraction"] == "Calculated (verify)"
    assert row["Background calculated"] == "2026-09-13T09:00:00Z"
    assert row["Background QC flags"] == "0"
    assert row["Cultivation IDs"] == "Partial 3/5"
    assert row["Missing strain"] == "2"
    assert row["Experiment number"] == "—"


def test_custom_columns_cannot_replace_readiness_or_other_fixed_columns() -> None:
    run = SimpleNamespace(
        plate_id="plate-3",
        experiment_name="Actual experiment",
        plate_name="Plate",
        experiment_date="2026-09-14",
        project=None,
        strains=(),
        media=(),
        treatments=(),
        concentration_ranges=(),
        inoculum_ranges=(),
        custom_fields=(
            ("background subtraction", ("pretend ready",)),
            ("experiment", ("different",)),
            ("oxygen", ("aerobic",)),
        ),
        updated_at="2026-09-14T12:00:00Z",
        growth_readiness=GrowthRunReadiness(
            background_status="Needs recalculation",
            cultivation_status="Saved",
            cultivation_saved=92,
            cultivation_total=92,
            cultivation_experiment_number="07",
        ),
    )

    row = run_summary_rows((run,), ("Background subtraction", "EXPERIMENT", "Oxygen"))[0]

    assert row["Background subtraction"] == "Needs recalculation"
    assert row["Experiment"] == "Actual experiment"
    assert row["Cultivation IDs"] == "Saved 92/92"
    assert row["Experiment number"] == "07"
    assert row["Oxygen"] == "aerobic"
    assert "EXPERIMENT" not in row
