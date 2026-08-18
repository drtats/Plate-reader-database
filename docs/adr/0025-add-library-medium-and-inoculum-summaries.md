# ADR-0025: Add Library medium and inoculum summaries

- Status: accepted
- Date: 2026-08-17
- Owners: integration owner

## Context

The sortable Growth Run Library summarizes strains, treatments, and concentrations,
but users also need medium and inoculum size to distinguish candidate runs before
opening or comparing them. Loading full plate snapshots would make Library discovery
read measurements unnecessarily.

## Decision

The metadata-only `RunSummary` projection is extended additively with immutable media
and inoculum-range tuples. Media values are trimmed, deduplicated case-insensitively,
and displayed deterministically. Inoculum sizes are aggregated into minimum/maximum
ranges per normalized unit; values with missing units remain explicit and different
units are never combined.

The existing single bounded Run Library query selects these fields from
`well_conditions` for nonblank wells only. It continues to avoid raw and compressed
measurement tables. The Library table adds sortable **Media** and **Inoculum size**
columns while retaining staged browser-local row selection.

## Consequences

- Runs can be screened by medium and inoculum size before opening a workspace.
- Blank wells do not contribute misleading media or inoculum summaries.
- Unitless inoculum values display as `unit not set` instead of being discarded.
- No schema migration or measurement rewrite is required.

## Verification

- Repository contract tests cover normalization, blank exclusion, mixed units,
  missing units, empty plates, one-query behavior, and absence of measurement reads.
- UI tests cover the new columns and range formatting.
- The frozen contract manifest records the additive `RunSummary` amendment.
