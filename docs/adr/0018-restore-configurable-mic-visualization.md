# ADR-0018: Restore configurable MIC visualization

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The legacy MIC visualization could group the x-axis by multiple result or
metadata fields, assign color and marker shape independently, and use custom
well labels. The first new dot plot hard-coded treatment, strain, and MIC
operator even after the result-search backend began returning a typed dynamic
field catalog.

## Decision

Use the same authorized search result DTO and field catalog for tabular results
and dot-plot controls. Keep visualization options immutable and presentation
only: ordered grouping fields, optional color and symbol fields, and a requested
logarithmic MIC axis. Validate requested fields before plotting, build combined
group labels deterministically, and use deterministic jitter so Streamlit reruns
produce the same visual positions.

Automatically fall back to a linear y-axis when a result contains zero or a
negative value because Plotly cannot represent those values on a logarithmic
axis. The plot builder remains cached and performs no database access.

## Consequences

Any standard metadata or discovered custom label can drive the visualization
without adding field-specific UI code or SQL. Search filters, displayed columns,
plot grouping, navigation, and dot plots all operate on one complete result DTO.

## Verification

Unit tests cover multi-field/custom grouping, color, marker shape, deterministic
group order, logarithmic and linear axes, empty results, and unknown fields. The
existing end-to-end MIC Streamlit workflow still renders the default dot plot.
