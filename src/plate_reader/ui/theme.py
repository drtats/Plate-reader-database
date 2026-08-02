"""Per-session application appearance controls."""

from __future__ import annotations

import streamlit as st


def render_theme_control() -> bool:
    """Render the appearance switch and apply the selected session theme."""

    st.session_state.setdefault("dark_mode", False)
    dark_mode = st.sidebar.toggle(
        "Dark mode",
        key="dark_mode",
        help="Applies to this browser session. Streamlit's Settings menu remains available.",
    )
    if dark_mode:
        st.markdown(_DARK_CSS, unsafe_allow_html=True)
    return dark_mode


_DARK_CSS = """
<style id="plate-reader-dark-mode">
:root { color-scheme: dark; }
[data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background-color: #0e1117;
  color: #fafafa;
}
[data-testid="stSidebar"] { background-color: #161b22; }
[data-testid="stSidebar"] *, [data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] label, [data-testid="stAppViewContainer"] h1,
[data-testid="stAppViewContainer"] h2, [data-testid="stAppViewContainer"] h3 {
  color: #fafafa;
}
[data-testid="stForm"], [data-testid="stExpander"], [data-testid="stMetric"] {
  border-color: #30363d;
}
[data-baseweb="input"] > div, [data-baseweb="select"] > div,
[data-baseweb="textarea"] > div, [data-testid="stDataEditor"] {
  background-color: #161b22;
  color: #fafafa;
}
</style>
"""
