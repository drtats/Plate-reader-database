# ADR 0044: Show Growth readiness in metadata-only Library rows

Status: accepted, 2026-09-14.

Users need to locate missing cultivation IDs, incomplete strains and unavailable or
outdated background subtraction without loading each dataset. `RunSummary` adds an
optional immutable `GrowthRunReadiness`; non-Growth rows leave it unset. The shared
Library table displays the experiment number, saved cultivation count, missing-strain
count, background status, calculation timestamp and background QC-flag count.

The paginated search uses one metadata-only query, aggregating revision/result
availability without retrieving raw measurements or series chunks. Cultivation counts
exclude blank controls and describe saved availability, not a full validation of
identity fingerprints. Missing-strain nonblank wells stay in the denominator so they
cannot appear complete. Current background result QC flags are counted separately
from whether the revision matches its assignment inputs.

New background computations store `background_assignment_sha256` in existing revision
parameters, calculated from sorted (well ID, position, blank flag, background group) tuples.
This derived field overrides a same-named user parameter. Raw data is immutable after
import, so the Library can compare this metadata fingerprint without reading curves.
The existing full raw-plus-assignment input hash and workspace validation are unchanged.
Strain, treatment, cultivation IDs, display names and edit timestamps do not affect
background freshness. Well IDs participate so portable collision remapping is marked
stale consistently with workspace validation. No migration or automatic recomputation is introduced.

Background labels distinguish `Not calculated`, `No current revision`, `No results`,
`Calculated (verify)` for older revisions without a metadata fingerprint,
`Needs recalculation` for changed assignments, and `Current` for a matching revision
with results. Current describes input freshness, not acceptance of every QC result.
Older results are never represented as verified current merely because they exist.

Library refresh remains explicit through Search, preserving the existing selection
and caching behavior. All metadata queries are read-only and use the same adapters.
Tests cover current/stale recompute transitions, old revisions, empty results,
partial/missing cultivation assignments, pagination and no-raw single-query behavior.
