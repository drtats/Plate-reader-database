# ADR-0023: Separate run-library discovery from plate comparison

- Status: superseded in part by ADR-0024; the Run Library decision remains accepted
- Date: 2026-08-17
- Owners: integration owner

## Context

The Growth Run Library originally rendered one bordered card per run and used a
separate select box to open a workspace. It did not expose well-condition metadata,
and discovering related runs required opening plates individually. A sortable table
with compact strain, treatment, and concentration summaries makes discovery faster.

Cross-plate comparison is a different use case. It requires exact well-level condition
matching and can load measurements from several plates. Placing those controls directly
in the Library would mix cheap discovery queries with expensive analysis and would make
ordinary selection changes trigger avoidable Streamlit work.

## Decision

`RunSummary` is extended additively with immutable strain, treatment, and per-unit
concentration-range summaries. Repository adapters construct the projection with one
bounded metadata-only query. Blank wells do not contribute summary values, missing
metadata remains explicit, and differently-unit concentrations are never merged.

The Growth Run Library owns search, browser-side sorting, staged run selection, and
navigation. Its checkbox table is submitted through an explicit form action, so changing
a selection does not execute Python or query the database.

Plate-to-plate comparison is a separate navigation surface and application use case.
Condition discovery reads only plate, well, and well-condition records. Exact matching
uses selected metadata fields, with concentration and concentration unit treated as one
identity. Raw measurements are loaded only after an explicit render action.

## Consequences

- Library listing remains lightweight and does not load raw or compressed measurements.
- `RunSummary` and the repository protocol receive additive read-only fields/methods;
  the Phase 1 freeze manifest records this approved amendment.
- The Library can hand selected stable plate IDs to the comparison page without owning
  comparison controls or plots.
- A plate with missing condition metadata remains discoverable but cannot silently match
  another plate on the missing value.
- Comparison rendering may make one intentional measurement load per selected plate;
  changing table or condition selections does not.
- No schema migration is required. A new index is allowed only after measured query-plan
  or latency evidence shows that the existing plate/well/condition indexes are inadequate.

## Alternatives considered

- `st.dataframe` row-selection callbacks were rejected because selection events rerun
  Streamlit, contrary to the staged-interaction requirement.
- Loading each plate through the existing workspace service to build Library summaries
  was rejected because it creates N+1 queries and loads measurements unnecessarily.
- Embedding comparison configuration and plots in the Library was rejected because it
  couples discovery to analysis and makes state, caching, and failure behavior unclear.
- A custom JavaScript data-grid component was deferred because fixed-row
  `st.data_editor` inside a form supplies the required sorting and staged selection with
  less maintenance and accessibility risk.

## Verification

- Repository contract tests cover metadata aggregation, blank/missing values, mixed
  units, stable pagination, same-well filters, and condition-only comparison reads.
- Unit tests cover normalized common-condition intersections, exclusions, units, and
  replicate preservation.
- UI tests cover table formatting, stable hidden plate IDs, selection validation, and
  navigation handoff.
- A manual browser check confirms sorting and checkbox changes do not trigger a Streamlit
  rerun; only Search, Open, Compare, Find matches, and Render are server actions.
- The full Ruff, mypy, and Pytest suite remains green.
