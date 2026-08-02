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
:root {
  color-scheme: dark;
  --background-color: #0e1117;
  --secondary-background-color: #161b22;
  --text-color: #f0f2f6;
  --primary-color: #4f8cff;
}
[data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background-color: #0e1117;
  color: #f0f2f6;
}
[data-testid="stSidebar"] { background-color: #161b22; }
[data-testid="stSidebar"] *, [data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] label, [data-testid="stAppViewContainer"] h1,
[data-testid="stAppViewContainer"] h2, [data-testid="stAppViewContainer"] h3,
[data-testid="stWidgetLabel"] *, [data-testid="InputInstructions"] {
  color: #f0f2f6 !important;
}
[data-testid="stForm"], [data-testid="stExpander"], [data-testid="stMetric"] {
  border-color: #30363d;
}
[data-testid="stExpander"] summary {
  background-color: #161b22 !important;
  border-color: #30363d !important;
  color: #f0f2f6 !important;
}
[data-baseweb="input"] > div, [data-baseweb="select"] > div,
[data-baseweb="textarea"] > div, [data-baseweb="base-input"],
[data-testid="stNumberInputContainer"], [data-testid="stTextInputRootElement"] {
  background-color: #161b22 !important;
  border-color: #484f58 !important;
  color: #f0f2f6 !important;
}
[data-baseweb="input"] input, [data-baseweb="select"] input,
[data-baseweb="select"] div, [data-baseweb="textarea"] textarea,
[data-testid="stNumberInput"] input, [data-testid="stTextInput"] input {
  color: #f0f2f6 !important;
  -webkit-text-fill-color: #f0f2f6 !important;
}
[data-baseweb="select"] svg, [data-baseweb="input"] svg {
  color: #c9d1d9 !important;
  fill: #c9d1d9 !important;
}
[data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"],
[role="option"] {
  background-color: #161b22 !important;
  color: #f0f2f6 !important;
}
[data-baseweb="tag"] {
  background-color: #294a7a !important;
  color: #ffffff !important;
}
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button,
[data-testid="stFormSubmitButton"] button {
  background-color: #21262d !important;
  border-color: #586069 !important;
  color: #f0f2f6 !important;
}
[data-testid="stButton"] button *, [data-testid="stDownloadButton"] button *,
[data-testid="stFormSubmitButton"] button * {
  color: inherit !important;
}
button[kind="primary"] {
  background-color: #2563eb !important;
  border-color: #4f8cff !important;
  color: #ffffff !important;
}
button:disabled {
  background-color: #21262d !important;
  color: #8b949e !important;
  opacity: 0.8 !important;
}
[data-testid="stNumberInput"] button {
  background-color: #21262d !important;
  border-color: #484f58 !important;
  color: #f0f2f6 !important;
}
[data-testid="stDataFrame"] {
  background-color: #0d1117 !important;
  border: 1px solid #30363d !important;
}
[data-testid="stDataFrameResizable"] {
  border-color: #30363d !important;
}
.stDataFrameGlideDataEditor canvas {
  filter: invert(0.88) hue-rotate(180deg) contrast(0.94) !important;
}
[data-testid="stDataFrame"] textarea, [data-testid="stDataFrame"] input {
  background-color: #161b22 !important;
  color: #f0f2f6 !important;
}
[data-testid="stMarkdownContainer"] table {
  background-color: #0d1117 !important;
  color: #f0f2f6 !important;
}
[data-testid="stMarkdownContainer"] th {
  background-color: #21262d !important;
  color: #f0f2f6 !important;
}
[data-testid="stMarkdownContainer"] td {
  background-color: #0d1117 !important;
  border-color: #30363d !important;
  color: #f0f2f6 !important;
}
</style>
"""
