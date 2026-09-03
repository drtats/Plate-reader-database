# ADR-0031: Separate experiment metadata from well export rows

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner
- Supersedes: Growth metadata topology in ADR-0026 and `GROWTH_TABULAR_EXPORT_V1`

## Context

The v1 companion metadata CSV interleaved one `run` row with one row for every
well. In a multi-run export, experiment metadata therefore appeared periodically
inside a table dominated by well records. The mixed schema also required many
columns that were meaningful for only one of the two row types.

## Decision

Define Growth tabular export v2. `growth_runs_metadata.csv` is a homogeneous table
with exactly one experiment/run metadata row per selected run. It contains only
run identity, experiment metadata, source context, and retained metadata JSON.

All well, condition, and custom layout values remain in `growth_runs.csv`, whose
rows already carry their well context alongside each immutable observation. The
measurement CSV schema and scientific calculations are otherwise unchanged.

## Consequences

- Experiment rows no longer appear between blocks of well rows.
- The metadata row count equals the number of selected runs.
- Well metadata remains available with the measurements it describes.
- Consumers relying on the v1 mixed metadata topology must migrate to v2.

## Verification

- Unit tests freeze the homogeneous metadata headers and one-row-per-run topology.
- Multi-adapter integration tests reconcile two selected runs to two metadata rows
  and continue to prove that export performs no writes.
- The UI test reports the new experiment metadata row count.
