# ADR-0029: Show custom layout fields in the Growth Run Library

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner
- Extends: ADR-0025, ADR-0027

## Context

Assay-wide custom layout columns were available in editors and Growth exports but
not in the Run Library. Users therefore had to open each experiment to inspect a
custom condition. The Library must remain metadata-only and must not load raw or
compressed growth measurements.

## Decision

Extend the additive `RunSummary` read model with immutable custom-field summaries.
The existing bounded Library query reads well `custom_json` alongside condition
metadata and aggregates distinct nonempty values case-insensitively. Values from
blank wells are included for custom fields because a custom column may describe
plate structure rather than culture conditions; fixed strain, medium, treatment,
concentration, and inoculum summaries continue to exclude blank wells.

The Library obtains the assay-wide column definitions when it performs a search
and adds those columns to the sortable table. A run displays its distinct values
as a comma-separated summary or an em dash when it has none. The column catalog
and search results remain in browser session state, so checking or sorting rows
does not cause another backend query.

## Consequences

- Universal Growth layout fields are visible consistently in layouts, Library
  rows, and tabular exports.
- Library discovery still does not touch measurement tables.
- Complex JSON custom values use deterministic compact JSON in the summary.
- Wide tables may require horizontal scrolling when many custom columns exist.

## Verification

- Repository contract tests cover case-insensitive value aggregation, blank-well
  custom values, empty runs, the single-query invariant, and measurement isolation.
- UI tests cover populated and blank custom Library cells.
- Full formatting, typing, repository, UI, and coverage checks remain required.
