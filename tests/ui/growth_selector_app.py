"""Minimal Streamlit entry point for the real Growth selector component test."""

import streamlit as st

from plate_reader.domain.common.plate import PLATE_96
from plate_reader.ui.growth_selector import render_growth_well_selector

st.session_state["component_run_count"] = int(st.session_state.get("component_run_count", 0)) + 1
st.caption(f"Component runs: {st.session_state['component_run_count']}")

wells = tuple(
    {
        "position": position.label,
        "display_name": f"Sample {position.label}",
        "raw_label": f"raw-{position.label}",
        "strain": "strain-a" if position.row_index < 4 else "strain-b",
        "concentration": float(position.column_index + 1),
        "medium": "M9",
        "custom_json": '{"Oxygen":"low"}',
    }
    for position in PLATE_96.positions()
)

selected, render = render_growth_well_selector(
    wells,
    tuple(position.label for position in PLATE_96.positions()[:8]),
    state_key="component_growth_selection",
    form_key="component_growth_plot_form",
    render_form_controls=lambda _selected: st.form_submit_button("Render selected curves"),
    selection_submitted=bool,
)

if render:
    st.session_state["rendered_growth_selection"] = selected
if rendered := st.session_state.get("rendered_growth_selection"):
    st.caption(f"Rendered wells: {', '.join(rendered)}")
