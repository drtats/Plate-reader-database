# ADR-0026: Add a separate multi-run Growth tabular export

- Status: accepted
- Date: 2026-08-18
- Owners: integration owner

## Context

The existing Growth Workspace CSV downloads contain only the series prepared for
one visible plot. Laboratory analysis also needs a stable two-file format spanning
multiple complete Growth runs: one long observation table and one companion
run/well metadata table. The supplied reference keeps raw OD, matched background,
and corrected OD as separate columns and floors corrected values at `0.0001`.

The domain background contract deliberately leaves corrected values unclipped.
Cross-run selection also does not belong in a single-run workspace.

## Decision

Add a separate **Growth Data Export** navigation page. Its search and selection
remain metadata-only until the user explicitly prepares an export. The application
service loads each selected Growth plate through existing repository interfaces and
creates `growth_runs.csv` plus `growth_runs_metadata.csv` without database writes.

The observation CSV exposes immutable `Raw OD`, the current non-stale matched
`Background Mean OD`, and an export-only compatibility value calculated as
`max(0.0001, Raw OD - Background Mean OD)`. Missing or stale backgrounds leave
the derived cells blank and set an explicit QC flag/reason; zero is never silently
substituted. The compatibility floor does not change domain logic or persistence.

## Consequences

- Users can select any number of Growth runs and obtain the established analysis
  shape without changing plot selection.
- Raw and derived values remain distinguishable and auditable in every row.
- Native runs lacking legacy source start time or optional metadata export blanks
  plus visible warnings rather than invented values.
- No schema migration, raw-data rewrite, or portable-format change is required.

## Verification

- Unit tests freeze exact headers, encoding, ordering, formulas, QC behavior, and
  metadata row topology.
- Repository integration tests reconcile multi-run row counts on both local
  adapters and prove that all relevant table counts are unchanged by export.
- Streamlit tests prove that searching/selecting does not load raw observations
  and that both downloads appear only after explicit preparation.
