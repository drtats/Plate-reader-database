from __future__ import annotations

import csv
import io
from collections.abc import Mapping

import pytest

from plate_reader.application.services import (
    BuildGrowthDisplayNamesService,
    GrowthDisplayNameAction,
    GrowthDisplayNameOptions,
    GrowthDisplayNameScope,
    GrowthDisplayNameToken,
    GrowthDisplayNumberFormat,
    export_growth_display_name_csv,
    preview_growth_display_name_csv,
)
from plate_reader.domain.common.plate import PLATE_96


def test_formula_uses_ordered_fields_and_only_selected_wells() -> None:
    preview = BuildGrowthDisplayNamesService().execute(
        growth_wells(),
        {"experiment_name": "Experiment 1"},
        ("A2", "A1"),
        GrowthDisplayNameOptions(
            tokens=(
                well_token("strain"),
                well_token("treatment"),
                well_token("concentration"),
                well_token("concentration_unit"),
                well_token("replicate"),
            ),
            separator="_",
        ),
    )

    assert [change.position for change in preview.changes] == ["A1", "A2"]
    assert preview.changes[0].proposed_name == "strain-a_drug-a_0.125_ug/mL_2"
    assert preview.changes[0].action is GrowthDisplayNameAction.CHANGE
    assert preview.changes[1].proposed_name == "strain-a_1"
    assert preview.changes[1].action is GrowthDisplayNameAction.ADD
    assert (preview.changed_count, preview.overwrite_count, preview.clear_count) == (2, 1, 0)


def test_formula_supports_plate_custom_and_numeric_format_tokens() -> None:
    preview = BuildGrowthDisplayNamesService().execute(
        growth_wells(),
        {"experiment_name": "Exp", "tags": ("kinetics", "priority")},
        ("A1",),
        GrowthDisplayNameOptions(
            tokens=(
                plate_token("experiment_name"),
                plate_token("tags"),
                well_token("custom:Oxygen"),
                well_token("concentration"),
            ),
            separator="-",
            prefix="pre-",
            suffix="-post",
            number_format=GrowthDisplayNumberFormat.THREE_DECIMALS,
        ),
    )

    assert preview.changes[0].proposed_name == "pre-Exp-kinetics+priority-low-0.125-post"


def test_formula_can_preserve_empty_tokens_or_clear_an_existing_name() -> None:
    preserved_empty = BuildGrowthDisplayNamesService().execute(
        growth_wells(),
        {"experiment_name": "Exp"},
        ("A2",),
        GrowthDisplayNameOptions(
            tokens=(well_token("strain"), well_token("treatment")),
            separator="_",
            omit_empty=False,
        ),
    )
    cleared = BuildGrowthDisplayNamesService().execute(
        growth_wells(),
        {"experiment_name": "Exp"},
        ("A1",),
        GrowthDisplayNameOptions(tokens=(well_token("missing_value"),)),
    )

    assert preserved_empty.changes[0].proposed_name == "strain-a_"
    assert cleared.changes[0].action is GrowthDisplayNameAction.CLEAR
    assert (cleared.overwrite_count, cleared.clear_count) == (1, 1)


def test_formula_rejects_empty_duplicate_or_unknown_tokens() -> None:
    with pytest.raises(ValueError, match="at least one"):
        GrowthDisplayNameOptions(tokens=())
    with pytest.raises(ValueError, match="cannot be repeated"):
        GrowthDisplayNameOptions(tokens=(well_token("strain"), well_token("strain")))
    with pytest.raises(ValueError, match="Unknown Growth well"):
        BuildGrowthDisplayNamesService().execute(
            growth_wells(),
            {},
            ("A1",),
            GrowthDisplayNameOptions(tokens=(well_token("absent"),)),
        )
    with pytest.raises(ValueError, match="Unknown Growth plate"):
        BuildGrowthDisplayNamesService().execute(
            growth_wells(),
            {},
            ("A1",),
            GrowthDisplayNameOptions(tokens=(plate_token("absent"),)),
        )


def test_csv_export_is_excel_friendly_complete_and_quoted() -> None:
    artifact = export_growth_display_name_csv(growth_wells())

    assert artifact.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(artifact.decode("utf-8-sig"))))
    assert rows[0] == ["Well", "Display name"]
    assert len(rows) == 97
    assert rows[1] == ["A1", "existing,a1"]
    assert rows[-1] == ["H12", ""]


def test_partial_csv_changes_only_listed_wells_and_requires_clear_confirmation_data() -> None:
    preview = preview_growth_display_name_csv(
        growth_wells(),
        "Well,Display name\na02,updated a2\nA01,\n",
    )

    assert [change.position for change in preview.changes] == ["A1", "A2"]
    assert preview.changes[0].action is GrowthDisplayNameAction.CLEAR
    assert preview.changes[1].action is GrowthDisplayNameAction.ADD
    assert (preview.changed_count, preview.overwrite_count, preview.clear_count) == (2, 1, 1)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("Well,well,Display name\nA1,A1,name\n", "duplicate headers"),
        ("Well,Name\nA1,name\n", "requires Well and Display name"),
        ("Well,Display name\nA1,one\na01,two\n", "duplicate well"),
        ("Well,Display name\nZ1,name\n", "invalid well"),
        ("Well,Display name\n,orphan\n", "missing Well"),
        ("Well,Display name\n,\n", "contains no well rows"),
    ],
)
def test_csv_validation_rejects_ambiguous_or_invalid_files(content: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        preview_growth_display_name_csv(growth_wells(), content)


def test_csv_validation_rejects_non_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        preview_growth_display_name_csv(growth_wells(), b"\xff\xfe\x00")


def growth_wells() -> tuple[Mapping[str, object], ...]:
    overrides: dict[str, dict[str, object]] = {
        "A1": {
            "display_name": "existing,a1",
            "raw_label": "raw-a1",
            "strain": "strain-a",
            "treatment": "drug-a",
            "concentration": 0.125,
            "concentration_unit": "ug/mL",
            "medium": "M9",
            "grouping_label": "group-a",
            "inoculum_size": 0.02,
            "inoculum_unit": "OD600",
            "replicate": 2,
            "custom_fields": {"Oxygen": "low", "empty": None},
            "missing_value": None,
        },
        "A2": {
            "display_name": "",
            "strain": "strain-a",
            "treatment": None,
            "concentration": None,
            "concentration_unit": None,
            "replicate": 1,
            "custom_fields": {"Oxygen": "high"},
        },
    }
    return tuple(
        {
            "position": position.label,
            "display_name": "",
            "custom_fields": {},
            **overrides.get(position.label, {}),
        }
        for position in PLATE_96.positions()
    )


def well_token(field: str) -> GrowthDisplayNameToken:
    return GrowthDisplayNameToken(GrowthDisplayNameScope.WELL, field)


def plate_token(field: str) -> GrowthDisplayNameToken:
    return GrowthDisplayNameToken(GrowthDisplayNameScope.PLATE, field)
