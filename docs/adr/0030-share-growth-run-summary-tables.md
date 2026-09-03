# ADR-0030: Share Growth run-summary columns across discovery and export

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner
- Extends: ADR-0026, ADR-0029

## Context

The Growth Run Library displayed condition summaries and universal custom layout
fields, while the Growth Data Export selector showed only basic run identity. A
user choosing complete runs for export could not see the same metadata needed to
distinguish those runs without returning to the Library.

## Decision

Use one metadata-only table formatter for the Growth Run Library and Growth Data
Export selector. Both surfaces display strain, media, treatment, concentration,
inoculum, and every assay-wide custom-column summary from `RunSummary`.

The export page loads and caches the assay-wide column catalog with its bounded run
search. Selecting or sorting rows continues to use browser session state and does
not load raw observations. Layout or custom-column writes invalidate both discovery
caches so the next visit reflects current metadata.

## Consequences

- A run has the same summary columns and display formatting on both surfaces.
- Universal custom columns are visible before users prepare the CSV files that
  contain those columns.
- Wide selectors may require horizontal scrolling.
- Export preparation remains the only action on this page that loads measurements.

## Verification

- Shared formatter tests cover populated, blank, numeric-range, and custom fields.
- The Growth Data Export Streamlit test asserts the complete selector schema and
  custom values while retaining the no-measurement-load assertion before Prepare.
