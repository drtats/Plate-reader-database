# ADR-0024: Select individual wells for plate comparison

- Status: accepted
- Date: 2026-08-17
- Owners: integration owner
- Supersedes: the common-condition matching decision in ADR-0023

## Context

ADR-0023 separated plate comparison from the Run Library, but its initial workflow
required a condition to occur on every source plate. That intersection model prevents
users from combining intentionally different strains, treatments, concentrations, or
controls in one plot. It also makes the condition, rather than the well, the selectable
identity.

The required workflow is exploratory: filter one or more source runs by well metadata,
add any matching wells to a persistent selection, change the filters, add more wells,
and render the accumulated selection only when it is ready.

## Decision

Plate Comparison uses a metadata-only well index for the source plates selected in the
Run Library. The index is cached by the exact ordered source-plate IDs. Search filters
are pure application logic over that index; text/filter widget changes and table
selection remain inside Streamlit forms and do not execute Python until an explicit
action is submitted.

Each selected well is identified by the stable pair `(plate_id, well_id)`. Successive
searches add to an ordered, deduplicated plot-selection basket. Source-set changes clear
search, basket, and plot state. Blank wells are excluded by default but can be included
explicitly. Multiple values within one filter field are OR conditions; populated filter
fields combine with AND semantics.

Raw measurements are loaded only by **Render comparison curves**, only for plates
represented in the basket, and once per represented plate. Rendering requires wells
from at least two plates. Comparison remains raw-only in this first version.

## Consequences

- Different conditions may be intentionally combined in one plot; no shared-condition
  intersection is required.
- Filter and checkbox edits remain responsive and do not query measurements or write
  application state.
- The cached metadata index is bounded by the existing 100-plate selection limit (9,600
  wells for 96-well plates).
- Display name, grouping label, inoculum, strain, treatment, concentration/unit, medium,
  replicate, blank state, and source-run identity are available for filtering or review.
- Arbitrary `custom_json` fields are deferred; the first version filters first-class
  metadata only.
- No schema migration is required.

## Verification

- Unit tests cover filter normalization, OR/AND behavior, blank handling, bounded
  results, stable deduplication, stale-well rejection, and per-plate raw loading.
- Repository contract tests prove the index query is metadata-only, ordered, and
  includes the required first-class fields.
- UI tests cover successive searches, persistent basket selection, removal, source
  invalidation, and raw loading only after explicit render.
- Ruff, mypy, and the complete Pytest suite must pass before integration.
