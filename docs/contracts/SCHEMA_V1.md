# Schema contract v1

Status: frozen candidate, 2026-08-01. Authoritative DDL:
`migrations/0001_schema_v1.sql`.

## Design decisions

- Application-generated text IDs are stable across local databases, Turso, and
  portable exports. Names and filenames are editable labels, never identities.
- One experiment can contain any number of growth and MIC plates.
- One physical well is unique by both position and row/column within a plate.
- Growth time identity is `(plate, well, channel, time_index)`; precise elapsed
  time is stored once as integer microseconds. Floating-point minutes are derived
  for presentation and never used as the sole key.
- Raw growth and MIC observations are database-enforced immutable. Corrected
  values, calls, backgrounds, metrics, and MICs belong to versioned analysis
  revisions.
- A metadata-only change updates experiment/plate/well metadata plus provenance;
  it cannot update or replace raw tables.
- Soft deletion is represented by `plates.deleted_at/deleted_by`. No routine UI
  workflow physically deletes scientific records.
- Import idempotency is explicit in `import_sources.idempotency_key`. A deliberate
  new version receives a new plate/source ID and records its ancestry.
- All timestamps are UTC ISO-8601 text. All JSON is validated and canonicalized by
  application services before persistence.

## Table ownership

| Area | Tables | Purpose |
| --- | --- | --- |
| Schema/security | `schema_migrations`, `users` | Ordered migration history and application roles |
| Experiment core | `experiments`, `experiment_tags`, `plates`, `wells`, `well_conditions` | Shared searchable scientific context and physical layout |
| Imports | `import_sources` | Content hash, parser version, idempotency, and source status |
| Growth raw | `growth_measurements` | Immutable time-series observations |
| MIC raw | `mic_readings` | Immutable endpoint observations |
| Revision core | `analysis_revisions` | Algorithm/version/parameters/input hash/actor |
| Growth derived | `growth_backgrounds`, `growth_metrics` | Time-varying background QC and optional summary metrics |
| MIC derived | `mic_well_calls`, `mic_results` | Per-well calls and grouped MIC interpretations |
| Reuse/UI | `plate_templates`, `saved_options` | Reusable layouts and controlled suggestions |
| Audit | `provenance_events` | Append-only actor/event history |

## First-class search fields

Fields used for routine filtering, grouping, uniqueness, authorization, or
scientific interpretation are columns:

- experiment name/date/project/operator, tags, reader, incubation time, inoculum
  OD, growth phase, harvest OD, and doubling time;
- plate assay, name, status, instrument, channel, temperature, manual subtraction,
  MIC threshold/method, background method, lock/check/delete state;
- well position, label, blank/background group, plot selection, strain, medium,
  replicate, inoculum, treatment, concentration, and units;
- raw identity/value fields and all derived result fields used by plots/search.

Legacy-only source hints, arbitrary added labels, future assay metadata, and
unknown legacy columns are losslessly namespaced in `custom_json`. Import reports
list every field placed there; nothing is silently discarded.

## Lifecycle and write rules

1. `draft` plates can receive metadata/layout changes. Raw data is inserted only
   by the original atomic import.
2. `final` plates retain metadata correction workflows but require an explicit
   reason/provenance event; raw data remains immutable.
3. `archived` or soft-deleted plates are read-only except for an admin restore.
4. Creating a current analysis revision marks the prior revision for that
   plate/algorithm non-current in the same transaction, then inserts all new
   derived rows and provenance.
5. Importing a plate inserts source, experiment/plate, 96 wells/conditions, raw
   observations, and provenance in one transaction. Any failure rolls back every
   application row.
6. Repository adapters enable foreign keys on every connection and use parameter
   binding. Streamlit pages never run migrations or SQL.

## Query and index plan

The contract tests run `EXPLAIN QUERY PLAN` and require these indexes:

| Operation | Predicate/order | Required index |
| --- | --- | --- |
| Run listing | active plates ordered by newest update | `idx_plates_list` |
| Experiment/project history | project and date | `idx_experiments_project` |
| Plate loading | plate/channel ordered by time and well | `idx_growth_measurements_load` |
| Background plotting | revision/channel/time | `idx_growth_backgrounds_load` |
| Layout/filter search | strain, medium, treatment, concentration | `idx_conditions_search` |
| MIC search | strain, treatment, medium, MIC | `idx_mic_results_search` |
| Audit view | entity type/ID and newest event | `idx_provenance_entity` |

Run-list queries select run columns plus compact summaries derived from nonblank
`well_conditions`; they never select raw or compressed measurements. Plate-to-plate
comparison builds a separate bounded metadata index over experiments, plates, wells, and
conditions. Filtering and selection operate on that index. Full measurements are loaded
only after a workspace is opened or a comparison plot is explicitly rendered, streamed in
bounded chunks by repository adapters, and cached by immutable plate/revision identity.

## Size and Turso budget check

The integration fixture creates 145 timepoints (0 through 24 hours at 10-minute
intervals) for 96 wells: **13,920 measurement rows**. After `VACUUM`, the complete
version-1 SQLite file with schema and indexes measured **2,293,760 bytes
(approximately 2.19 MiB)**. The automated gate requires less than 3 MB.

As checked on 2026-08-01, Turso's free plan lists 5 GB storage, 500 million monthly
rows read, and 10 million monthly rows written. Turso counts scanned rows rather
than only returned rows, so indexed and bounded queries matter. See
[Turso pricing](https://turso.tech/pricing) and
[Turso usage and billing](https://docs.turso.tech/help/usage-and-billing).

Conservative implications before images/attachments (which are out of scope):

- under 15,000 application rows written for a typical one-channel run, allowing
  roughly 660 such new runs in a month before the write quota alone;
- about 2,100 typical runs before 5 GB is approached at the measured uncompressed
  file size;
- more than 33,000 complete 15,000-row run scans per month before the read quota,
  with normal summary queries and Streamlit caching using much less.

These are capacity estimates, not guarantees. Phase 5 must measure real Turso
usage and latency. If real data approaches the storage gate, a future migration
may encode immutable series in chunked arrays; version 1 keeps normalized rows
because they are transparent, directly queryable, portable, and currently well
inside the free limits.

## Required review answers

- **Can both legacy UIs' metadata be preserved?** Yes. Every observed field has a
  first-class column or a named `custom_json` destination documented above.
- **Can metadata edits avoid raw rewrites?** Yes; separate tables and immutable
  triggers enforce this, and tests compare raw hashes across a rename.
- **Are imports transactional/idempotent?** The schema provides foreign keys and
  a unique idempotency key; repository contract tests in Phase 3 must verify both
  adapters' transaction behavior.
- **Are time and well identities stable?** Yes; stable IDs, unique physical
  positions, integer indices, and integer elapsed microseconds avoid filename and
  float identity.
- **Can one experiment mix plate/assay types?** Yes; assay belongs to each plate.
- **Can old exports be identified without filenames?** Yes; legacy importers match
  table/column fingerprints and reject ambiguous shapes.
- **Can analyses be recomputed without raw changes?** Yes; revisions and derived
  tables are separate and raw-table mutation is blocked by triggers.
