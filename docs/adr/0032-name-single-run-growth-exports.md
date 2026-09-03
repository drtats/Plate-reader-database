# ADR-0032: Name single-run Growth exports from experiment identity

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner
- Extends: ADR-0026, ADR-0031

## Context

The established laboratory Growth file uses the experiment name plus its short
run hash, such as `experiment_name_dbea359c.csv`. The multi-run export initially
used `growth_runs.csv` for every selection size, so separately downloaded
single-run files were difficult to identify and could overwrite one another.

## Decision

For exactly one selected run, name the observation file from the safely normalized
experiment name plus a stable eight-character run hash. Give the experiment
metadata companion the same stem plus `_metadata`. Preserve an existing hexadecimal
short Run ID as the suffix; deterministically reduce other stable run identities.

Keep the generic `growth_runs.csv` and `growth_runs_metadata.csv` names when two or
more runs are selected because no single experiment name represents the bundle.

## Consequences

- Single-run downloads are recognizable and match the supplied output convention.
- Unsafe path and filename characters cannot escape into download names.
- Both files from one preparation sort together by their shared stem.
- Multi-run automation retains the existing generic filenames.

## Verification

- Unit tests reproduce the supplied experiment-name/hash pattern exactly.
- Tests cover the metadata companion name and generic multi-run fallback.
- Streamlit download buttons use the artifact filenames rather than fixed labels.
