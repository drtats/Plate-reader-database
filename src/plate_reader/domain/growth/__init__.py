"""Pure growth-curve parsing, background, and correction APIs."""

from plate_reader.domain.growth.background import calculate_backgrounds, subtract_background
from plate_reader.domain.growth.models import (
    GROWTH_BACKGROUND_VERSION,
    GROWTH_NORMALIZATION_VERSION,
    GrowthBackground,
    GrowthBackgroundResult,
    GrowthCorrectionResult,
    GrowthMeasurement,
    GrowthNormalizationResult,
    NormalizationSettings,
    WellBackgroundAssignment,
    WellLabel,
)
from plate_reader.domain.growth.parsing import parse_growth_csv, parse_label_layout

__all__ = [
    "GROWTH_BACKGROUND_VERSION",
    "GROWTH_NORMALIZATION_VERSION",
    "GrowthBackground",
    "GrowthBackgroundResult",
    "GrowthCorrectionResult",
    "GrowthMeasurement",
    "GrowthNormalizationResult",
    "NormalizationSettings",
    "WellBackgroundAssignment",
    "WellLabel",
    "calculate_backgrounds",
    "parse_growth_csv",
    "parse_label_layout",
    "subtract_background",
]
