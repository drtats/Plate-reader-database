# ADR-0019: Use dependency-free vector PDF export for Growth plots

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The Growth compatibility contract requires PNG and PDF downloads. Plotly's
browser modebar already provides high-resolution PNG, but server-side Plotly PDF
export normally requires Kaleido and a compatible Chrome installation. That
adds a relatively heavy and failure-prone runtime requirement to the free
Streamlit deployment.

## Decision

Render the already prepared `GrowthPlotData` DTO into a small landscape vector
PDF using only the Python standard library. The exporter applies the same plot
limits and symmetric-log transform as the interactive figure, groups and sorts
curves deterministically, clips them to the plot area, includes a bounded
legend, escapes user text, and returns an immutable filename/content artifact.

Generate the artifact only when the user renders selected curves. Keep Plotly's
client-side high-resolution PNG action and expose the PDF as a normal Streamlit
download. The exporter performs no SQL and never changes raw or derived data.

## Consequences

PNG and PDF export work in standalone, fake-cloud, and Streamlit Cloud modes
without Chrome, Kaleido, Matplotlib, or ReportLab. The PDF uses a portable Base14
font; unsupported characters are replaced rather than embedding a large font.

## Verification

Unit tests verify the PDF header/trailer and cross-reference offset, vector page
objects, escaped titles, stable filenames, corrected/raw labels, linear and
symmetric-log modes, bounded legends, and invalid input. The full Growth UI
smoke test confirms that rendering selected curves prepares a PDF artifact.
