"""Minimal Streamlit entry point for the real dual-view editor smoke test."""

from plate_reader.application.demo import synthetic_mic_csv
from plate_reader.domain.mic import parse_mic_plate_csv
from plate_reader.ui.plate_editor import mic_layout_frame, render_plate_editor

render_plate_editor(
    mic_layout_frame(parse_mic_plate_csv(synthetic_mic_csv())),
    state_key="component_layout",
    assay="mic",
)
