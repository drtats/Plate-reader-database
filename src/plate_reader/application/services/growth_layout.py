"""Pure Growth layout transformations used by application-facing UI actions."""

from __future__ import annotations

from enum import StrEnum

from plate_reader.application.contracts import WellLayoutChange
from plate_reader.domain.common.plate import PLATE_96


class GrowthBackgroundGroupSource(StrEnum):
    MEDIUM = "medium"
    STRAIN = "strain"
    GROUP = "grouping_label"
    TREATMENT = "treatment"


class BuildGrowthBackgroundGroupsService:
    """Derive complete background-group updates from one persisted well field."""

    def execute(
        self,
        wells: tuple[dict[str, object], ...],
        source: GrowthBackgroundGroupSource,
    ) -> tuple[WellLayoutChange, ...]:
        expected = tuple(position.label for position in PLATE_96.positions())
        by_position = {str(well.get("position", "")): well for well in wells}
        if len(wells) != 96 or set(by_position) != set(expected):
            raise ValueError("Growth background assignment requires every A1-H12 well")
        return tuple(
            WellLayoutChange(
                position=position,
                background_group=_group_value(by_position[position].get(source.value)),
            )
            for position in expected
        )


def _group_value(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return text or "plate"
