"""Metadata-only background freshness matches the scientific assignment inputs."""

from copy import deepcopy

from plate_reader.domain.growth.readiness import background_assignment_hash


def test_background_assignment_hash_ignores_order_and_unrelated_metadata() -> None:
    wells = [
        {
            "well_id": "w1",
            "position": "A1",
            "is_blank": True,
            "background_group": None,
            "strain": "MG1655",
        },
        {
            "well_id": "w2",
            "position": "A2",
            "is_blank": False,
            "background_group": "plate",
            "strain": "MG1655",
        },
    ]
    original = deepcopy(wells)
    expected = background_assignment_hash(wells)
    reordered = list(reversed(deepcopy(wells)))
    reordered[0].update(strain="acrB MG", Cultivation="ST-EXP-acrB_MG-MP96A0101R1")
    reordered[1]["background_group"] = "plate"
    assert background_assignment_hash(reordered) == expected
    assert wells == original
    reordered[0]["is_blank"] = True
    assert background_assignment_hash(reordered) != expected
    reordered[0]["is_blank"] = False
    reordered[0]["background_group"] = "M9"
    assert background_assignment_hash(reordered) != expected


def test_background_assignment_hash_detects_portable_well_remapping() -> None:
    wells = [
        {"well_id": "original", "position": "A1", "is_blank": True, "background_group": "plate"}
    ]
    old = background_assignment_hash(wells)
    wells[0]["well_id"] = "remapped"
    assert background_assignment_hash(wells) != old
