from __future__ import annotations

import pytest

from plate_reader.ui.plotting import MicDotPlotOptions, mic_result_dot_plot

RESULTS = (
    {
        "strain": "S1",
        "treatment": "drug-a",
        "medium": "LB",
        "replicate": 1,
        "mic_operator": "=",
        "mic_value": 2.0,
        "mic_unit": "ug/mL",
        "plate_name": "Plate 1",
        "experiment_date": "2026-01-01",
        "custom.host": "human",
    },
    {
        "strain": "S2",
        "treatment": "drug-a",
        "medium": "LB",
        "replicate": 1,
        "mic_operator": ">",
        "mic_value": 4.0,
        "mic_unit": "ug/mL",
        "plate_name": "Plate 2",
        "experiment_date": "2026-01-02",
        "custom.host": "mouse",
    },
)


def test_mic_dot_plot_supports_dynamic_group_color_shape_and_log_axis() -> None:
    figure = mic_result_dot_plot.__wrapped__(
        RESULTS,
        "result-key",
        MicDotPlotOptions(
            group_by=("treatment", "custom.host"),
            color_by="strain",
            symbol_by="mic_operator",
        ),
    )

    assert figure.layout.title.text == "MIC distribution by group"
    assert figure.layout.yaxis.type == "log"
    assert list(figure.layout.xaxis.ticktext) == ["drug-a | human", "drug-a | mouse"]
    assert {trace.name for trace in figure.data} == {"S1, =", "S2, >"}


def test_mic_dot_plot_handles_global_linear_empty_and_unknown_fields() -> None:
    linear = mic_result_dot_plot.__wrapped__(
        ({**RESULTS[0], "mic_value": 0.0},),
        "zero",
        MicDotPlotOptions(group_by=(), color_by=None, log_y=True),
    )
    empty = mic_result_dot_plot.__wrapped__((), "empty", MicDotPlotOptions())

    assert linear.layout.yaxis.type == "linear"
    assert list(linear.layout.xaxis.ticktext) == ["All data"]
    assert empty.layout.title.text == "No MIC results match the filters"
    with pytest.raises(ValueError, match="unavailable"):
        mic_result_dot_plot.__wrapped__(
            RESULTS,
            "missing",
            MicDotPlotOptions(group_by=("not-a-field",)),
        )
