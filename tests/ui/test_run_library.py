"""Focused tests for the metadata-only Growth Run Library table."""

from __future__ import annotations

from types import SimpleNamespace

from plate_reader.ui.pages import _run_library_rows, _run_library_table


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

    row = _run_library_rows((run,))[0]

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
        updated_at="2026-08-17T12:00:00Z",
    )

    table = _run_library_table((run,))

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
