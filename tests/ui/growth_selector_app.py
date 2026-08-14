"""Minimal Streamlit entry point for the real Growth selector component test."""

import streamlit as st

from plate_reader.domain.common.plate import PLATE_96
from plate_reader.ui.growth_selector import render_growth_well_selector

wells = tuple(
    {
        "position": position.label,
        "display_name": f"Sample {position.label}",
        "raw_label": f"raw-{position.label}",
        "strain": "strain-a" if position.row_index < 4 else "strain-b",
        "medium": "M9",
        "custom_json": '{"Oxygen":"low"}',
    }
    for position in PLATE_96.positions()
)

selected = render_growth_well_selector(
    wells,
    tuple(position.label for position in PLATE_96.positions()[:8]),
    state_key="component_growth_selection",
)

if st.button("Render selected curves"):
    st.session_state["rendered_growth_selection"] = selected
