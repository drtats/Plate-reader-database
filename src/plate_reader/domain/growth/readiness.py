"""Metadata fingerprints for Growth Library status, without loading observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

BACKGROUND_ASSIGNMENT_HASH_KEY = "background_assignment_sha256"


def background_assignment_hash(wells: Sequence[Mapping[str, object]]) -> str:
    """Fingerprint only blank/group inputs; raw measurements are immutable after import.

    Well identity also participates because portable remapping invalidates the
    workspace's full input hash. Strain, treatment, cultivation IDs and display
    labels cannot invalidate a background revision. This complements, rather than
    replaces, its full input hash.
    """

    assignments = sorted(
        (
            str(well["well_id"]),
            str(well["position"]),
            bool(well["is_blank"]),
            str(well.get("background_group") or "plate"),
        )
        for well in wells
    )
    return hashlib.sha256(json.dumps(assignments, separators=(",", ":")).encode()).hexdigest()
