from __future__ import annotations

import pytest

from plate_reader.application.contracts import AssayType
from plate_reader.ui.option_controls import option_fields


def test_saved_option_types_map_to_assay_specific_editor_columns() -> None:
    growth = {field.option_type: field.column for field in option_fields(AssayType.GROWTH)}
    mic = {field.option_type: field.column for field in option_fields(AssayType.MIC)}

    assert growth["treatment"] == "Treatment"
    assert mic["treatment"] == "Antibiotic / treatment"
    assert growth["medium"] == mic["medium"] == "Media"
    assert "background_group" in growth
    assert "background_group" not in mic


def test_saved_options_reject_mixed_assay_editor() -> None:
    with pytest.raises(ValueError, match="not supported"):
        option_fields(AssayType.MIXED)
