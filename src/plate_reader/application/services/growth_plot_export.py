"""Dependency-free vector PDF export for prepared Growth plot DTOs."""

from __future__ import annotations

import math
from dataclasses import dataclass

from plate_reader.application.services.growth_plot_styles import (
    GrowthPlotStyles,
    GrowthSeriesStyle,
    default_growth_plot_styles,
)
from plate_reader.application.services.growth_plotting import GrowthPlotData, GrowthPlotPoint


@dataclass(frozen=True, slots=True)
class GrowthPdfOptions:
    title: str = ""
    x_max: float = 1_400.0
    y_min: float = 0.001
    y_max: float = 1.5
    symlog: bool = True

    def __post_init__(self) -> None:
        values = (self.x_max, self.y_min, self.y_max)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("PDF plot limits must be finite")
        if self.x_max <= 0:
            raise ValueError("PDF X maximum must be greater than zero")
        if self.y_min >= self.y_max:
            raise ValueError("PDF Y minimum must be less than Y maximum")


@dataclass(frozen=True, slots=True)
class GrowthPdfArtifact:
    filename: str
    content: bytes


def export_growth_plot_pdf(
    plot_data: GrowthPlotData,
    options: GrowthPdfOptions,
    filename_source: str,
    styles: GrowthPlotStyles | None = None,
) -> GrowthPdfArtifact:
    """Render prepared curves into a landscape, vector-only PDF artifact."""

    if not plot_data.points:
        raise ValueError("At least one Growth plot point is required for PDF export")
    commands = _pdf_plot_commands(plot_data, options, styles)
    filename = f"{_safe_filename(filename_source) or 'growth-plot'}.pdf"
    return GrowthPdfArtifact(filename, _assemble_pdf(commands))


def _pdf_plot_commands(
    plot_data: GrowthPlotData,
    options: GrowthPdfOptions,
    styles: GrowthPlotStyles | None,
) -> bytes:
    page_height = 612.0
    left, bottom, width, height = 62.0, 68.0, 558.0, 462.0
    y_transform = _symlog if options.symlog else float
    transformed_min = y_transform(options.y_min)
    transformed_max = y_transform(options.y_max)

    def x_position(value: float) -> float:
        return left + value / options.x_max * width

    def y_position(value: float) -> float:
        transformed = y_transform(value)
        return (
            bottom + (transformed - transformed_min) / (transformed_max - transformed_min) * height
        )

    commands = [
        "1 1 1 rg 0 0 792 612 re f",
        "0 0 0 RG 0.8 w",
        f"{left:.2f} {bottom:.2f} {width:.2f} {height:.2f} re S",
        _pdf_text(left, page_height - 38, options.title.strip() or "Growth curves", 16),
        _pdf_text(left + width / 2 - 32, 28, "Time (minutes)", 10),
        _pdf_text(8, bottom + height / 2, "OD (symmetric log)" if options.symlog else "OD", 9),
    ]
    for index in range(5):
        fraction = index / 4
        x_value = fraction * options.x_max
        x = left + fraction * width
        commands.extend(
            (
                f"{x:.2f} {bottom:.2f} m {x:.2f} {bottom - 4:.2f} l S",
                _pdf_text(x - 12, bottom - 18, f"{x_value:g}", 8),
            )
        )
        transformed = transformed_min + fraction * (transformed_max - transformed_min)
        y_value = _inverse_symlog(transformed) if options.symlog else transformed
        y = bottom + fraction * height
        commands.extend(
            (
                f"{left - 4:.2f} {y:.2f} m {left:.2f} {y:.2f} l S",
                _pdf_text(22, y - 3, f"{y_value:.3g}", 8),
            )
        )
    curves = _curves(plot_data, styles or default_growth_plot_styles(plot_data))
    commands.append(f"q {left:.2f} {bottom:.2f} {width:.2f} {height:.2f} re W n")
    for style, points in curves:
        color = _hex_rgb(style.color_hex)
        path = []
        for point_index, point in enumerate(points):
            operator = "m" if point_index == 0 else "l"
            path.append(
                f"{x_position(point.elapsed_minutes):.2f} {y_position(point.value):.2f} {operator}"
            )
        if len(path) > 1:
            commands.append(
                f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG 1.2 w " + " ".join(path) + " S"
            )
    commands.append("Q")
    legend_x, legend_y = 636.0, 520.0
    shown_curves = curves[:28]
    for index, (style, _points) in enumerate(shown_curves):
        color = _hex_rgb(style.color_hex)
        y = legend_y - index * 16
        commands.extend(
            (
                f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG 1.5 w "
                f"{legend_x:.2f} {y:.2f} m {legend_x + 15:.2f} {y:.2f} l S",
                _pdf_text(legend_x + 20, y - 3, style.legend_label[:24], 7),
            )
        )
    if len(curves) > len(shown_curves):
        commands.append(
            _pdf_text(legend_x, legend_y - len(shown_curves) * 16, f"+ {len(curves) - 28} more", 7)
        )
    state = "Background corrected" if plot_data.correction_requested else "Raw values"
    commands.append(_pdf_text(636, 548, state, 8))
    return ("\n".join(commands) + "\n").encode("latin-1", errors="replace")


def _curves(
    plot_data: GrowthPlotData,
    styles: GrowthPlotStyles,
) -> tuple[tuple[GrowthSeriesStyle, tuple[GrowthPlotPoint, ...]], ...]:
    grouped: dict[tuple[str, str], list[GrowthPlotPoint]] = {}
    for point in plot_data.points:
        grouped.setdefault((point.position, point.channel), []).append(point)
    style_keys = {(style.position, style.channel) for style in styles.styles}
    if len(style_keys) != len(styles.styles) or style_keys != set(grouped):
        raise ValueError("Growth PDF styles do not match prepared series")
    return tuple(
        (
            style,
            tuple(
                sorted(
                    grouped[(style.position, style.channel)],
                    key=lambda point: (point.time_index, point.elapsed_microseconds),
                )
            ),
        )
        for style in styles.styles
    )


def _hex_rgb(value: str) -> tuple[float, float, float]:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError(f"Invalid Growth series color: {value}")
    try:
        red = int(value[1:3], 16) / 255
        green = int(value[3:5], 16) / 255
        blue = int(value[5:7], 16) / 255
    except ValueError as error:
        raise ValueError(f"Invalid Growth series color: {value}") from error
    return red, green, blue


def _assemble_pdf(content: bytes) -> bytes:
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 792 612] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii"))
        result.extend(value)
        result.extend(b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(result)


def _pdf_text(x: float, y: float, value: str, size: int) -> str:
    safe = (
        value.encode("cp1252", errors="replace")
        .decode("latin-1")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    return f"BT /F1 {size} Tf {x:.2f} {y:.2f} Td ({safe}) Tj ET"


def _safe_filename(value: str) -> str:
    normalized = "-".join(value.strip().lower().split())
    return "".join(
        character for character in normalized if character.isalnum() or character in "-_"
    )


def _symlog(value: float, *, linear_threshold: float = 0.01) -> float:
    return math.copysign(math.log10(1 + abs(value) / linear_threshold), value)


def _inverse_symlog(value: float, *, linear_threshold: float = 0.01) -> float:
    return math.copysign(linear_threshold * (10 ** abs(value) - 1), value)
