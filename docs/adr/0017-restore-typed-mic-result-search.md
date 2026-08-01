# ADR-0017: Restore typed MIC result search and selectable columns

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The legacy MIC app could discover custom well labels, filter joined result and
metadata fields, choose displayed columns, and open a matching plate. The first
new MIC page exposed only four fixed filters and rendered fixed result cards.
Recreating legacy SQL inside Streamlit would violate the repository boundary and
would make user-selected field names unsafe to interpolate.

## Decision

Expose a role-checked MIC result-search catalog from the application layer. The
catalog contains canonical standard fields, discovered custom well fields, and
available strain/treatment values. Keep SQL construction in the repository,
validate every requested field against the catalog, bind all values, and use a
fixed standard-field map plus correlated JSON filtering for custom fields.

Return complete result DTO dictionaries enriched with the first physical well's
custom values for each MIC group. Streamlit may choose presentation columns from
that DTO, but performs no SQL. Preserve bounded pagination and add navigation
from a result back to the existing MIC workspace.

## Consequences

Metadata and custom-label search can expand without changing the shared plate
editor or leaking storage concerns into the UI. Dynamic identifiers are never
inserted into SQL. Column selection affects presentation only, so dot plots and
navigation continue to receive complete result records.

## Verification

Integration tests cover catalog discovery, multi-value strain/treatment filters,
standard metadata filters, custom-label filters, enriched result values, and
unknown-field rejection on both local and fake-cloud adapters. Streamlit smoke
coverage checks selectable columns, the results table, dot plotting, and opening
the selected plate.
