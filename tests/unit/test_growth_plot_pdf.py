from __future__ import annotations

import pytest

from plate_reader.application.services import (
    GrowthPdfOptions,
    GrowthPlotData,
    GrowthPlotPoint,
    export_growth_plot_pdf,
)


def test_growth_pdf_is_valid_vector_artifact_with_safe_name_and_escaped_title() -> None:
    data = GrowthPlotData(
        (
            GrowthPlotPoint("A1", "control", 10.0, "od600", 0.2, 0.3, 0.1, True),
            GrowthPlotPoint("A1", "control", 0.0, "od600", -0.01, 0.09, 0.1, True),
            GrowthPlotPoint("A2", "A2", 0.0, "od600", 0.1, 0.1, 0.0, False),
            GrowthPlotPoint("A2", "A2", 10.0, "od600", 0.4, 0.4, 0.0, False),
        ),
        (),
        True,
    )

    artifact = export_growth_plot_pdf(
        data,
        GrowthPdfOptions(title=r"Growth (A) \\ test", x_max=60, y_min=-0.1, y_max=1.0),
        "My Growth: 24 h / test",
    )

    assert artifact.filename == "my-growth-24-h--test.pdf"
    assert artifact.content.startswith(b"%PDF-1.4")
    assert artifact.content.endswith(b"%%EOF\n")
    assert b"/Type /Page" in artifact.content
    assert b"Growth \\(A\\) \\\\" in artifact.content
    xref_offset = int(artifact.content.rsplit(b"startxref\n", 1)[1].splitlines()[0])
    assert artifact.content[xref_offset:].startswith(b"xref\n")


def test_growth_pdf_handles_linear_many_curves_and_rejects_invalid_input() -> None:
    points = tuple(
        GrowthPlotPoint(
            f"A{index + 1}",
            f"curve-{index}",
            0.0,
            "od600",
            0.1,
            0.1,
            0.0,
            False,
        )
        for index in range(29)
    )
    artifact = export_growth_plot_pdf(
        GrowthPlotData(points, (), False),
        GrowthPdfOptions(x_max=10, y_min=0, y_max=1, symlog=False),
        "",
    )

    assert artifact.filename == "growth-plot.pdf"
    assert b"Raw values" in artifact.content
    assert b"+ 1 more" in artifact.content
    with pytest.raises(ValueError, match="At least one"):
        export_growth_plot_pdf(GrowthPlotData((), (), False), GrowthPdfOptions(), "empty")
    with pytest.raises(ValueError, match="X maximum"):
        GrowthPdfOptions(x_max=0)
    with pytest.raises(ValueError, match="Y minimum"):
        GrowthPdfOptions(y_min=1, y_max=1)
    with pytest.raises(ValueError, match="finite"):
        GrowthPdfOptions(x_max=float("inf"))
