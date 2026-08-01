from plate_reader.ui.mic_pages import _mic_result_table_html


def test_mic_result_table_uses_selected_columns_and_escapes_values() -> None:
    rendered = _mic_result_table_html(
        (
            {
                "strain": "strain <one>",
                "mic_value": 2.0,
                "warning": None,
            },
        ),
        ("strain", "mic_value", "warning"),
        {"strain": "Strain", "mic_value": "MIC value", "warning": "Warning"},
    )

    assert "<th>Strain</th>" in rendered
    assert "strain &lt;one&gt;" in rendered
    assert "<td>2.0</td>" in rendered
    assert "<td></td>" in rendered
    assert rendered.count("<th>") == 3
