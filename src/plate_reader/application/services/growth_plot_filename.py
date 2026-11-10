"""Filename conventions shared by Growth plot and plot-data downloads."""


def growth_plot_filename_stem(title: str) -> str:
    """Keep decimal doses distinguishable (6.2ug -> 6_2ug) before adding an extension."""
    normalized = "-".join(title.lower().split()).replace(".", "_")
    return "".join(char for char in normalized if char.isalnum() or char in "-_")
