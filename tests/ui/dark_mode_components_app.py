"""Visual harness for dark-mode controls that Streamlit renders outside AppTest."""

from __future__ import annotations

import streamlit as st

from plate_reader.domain.common.plate import PLATE_96
from plate_reader.ui.growth_selector import render_growth_well_selector
from plate_reader.ui.plate_editor import growth_layout_frame, render_plate_editor
from plate_reader.ui.theme import render_theme_control

st.set_page_config(page_title="Dark mode component check", layout="wide")
st.session_state.setdefault("dark_mode", True)
render_theme_control()
st.title("Dark mode component check")

plotting, layout, selection = st.tabs(("Plotting", "96-well layout", "Well selection"))
with plotting:
    st.selectbox("Curve label", ("Display name", "Strain", "Group"))
    st.multiselect("Selected labels", ("control", "sample-a", "sample-b"), default=("control",))
    limits = st.columns(3)
    limits[0].number_input("X maximum", value=1_400.0)
    limits[1].number_input("Y minimum", value=0.001, format="%.4f")
    limits[2].number_input("Y maximum", value=1.5, format="%.4f")
    st.button("Save well selection", type="primary")

with layout:
    render_plate_editor(
        growth_layout_frame(),
        state_key="dark_mode_layout",
        assay="growth",
    )

with selection:
    wells = tuple(
        {
            "position": position.label,
            "display_name": f"sample-{position.label}",
            "raw_label": position.label,
            "custom_json": "{}",
        }
        for position in PLATE_96.positions()
    )
    render_growth_well_selector(
        wells,
        tuple(position.label for position in PLATE_96.positions())[:8],
        state_key="dark_mode_selection",
        form_key="dark_mode_selection_form",
        render_form_controls=lambda _selected: st.form_submit_button("Render selected curves"),
        selection_submitted=bool,
    )
