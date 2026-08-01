# ADR-0015: Restore Growth overview and QC without eager rendering

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Growth v4 displayed a static 8x12 panel of all well curves plus background-group
CV summaries and detailed timepoint statistics. The new workspace initially
showed only a final-OD heatmap and aggregate QC status counts. Eagerly rebuilding
96 Matplotlib axes on every Streamlit rerun also contributed to legacy slowness.

## Decision

Add an application service that validates and aggregates persisted current-
revision background rows by group and channel. The UI displays its group summary
and the persisted per-timepoint detail; it does not recompute scientific values.

Restore the 8x12 curve overview as an on-demand cached Plotly/WebGL figure. Build
its data with the same `PrepareGrowthPlotDataService` used by selected curves, so
raw/corrected fallback behavior and warnings remain identical. Cache inputs
include the immutable raw hash and selected revision key.

## Consequences

The legacy visual and QC detail are available again without slowing normal
workspace navigation. Endpoint heatmap, 96-well overview, and selected detailed
curves coexist because they answer different questions. Layout and raw readings
remain unchanged.

## Verification

Service tests cover group/channel aggregation, CV/status counts, blank ranges,
empty input, and invalid persisted values. Figure tests verify physical A1-H12
subplot order, sorted time points, corrected labeling, and high-resolution export
dimensions. The full Growth UI workflow confirms the lazy overview action is
available.
