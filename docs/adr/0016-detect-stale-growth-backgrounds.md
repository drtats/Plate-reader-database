# ADR-0016: Detect stale Growth backgrounds before correction

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

A Growth background revision records an input hash over immutable readings plus
blank/background-group assignments. Layout edits are intentionally independent
of raw and revision rows, but the loader previously returned the latest stored
background rows even when its hash no longer matched the edited layout. A plot
could therefore combine new assignments with stale background statistics.

## Decision

On every Growth load, compare the selected revision's stored `input_sha256` with
the deterministic hash of the current raw readings and assignments. Preserve the
historical revision, but withhold its background rows from correction and expose
an explicit stale flag until a new revision is computed.

Restore the legacy bulk shortcut through a pure application service that derives
all A1-H12 background-group updates from Media, Strain, Group, or Treatment. Save
those updates through the normal authorized, audited, optimistic-concurrency
layout service. Put both this action and recomputation beside the Overview QC
report.

## Consequences

Changing a blank flag or background group can never silently apply stale
correction. Users see raw values and a recompute warning until the new revision
commits. Historical revisions remain exportable and auditable by their original
input hashes.

## Verification

Integration tests compute backgrounds, change an assignment, prove the loader
withholds stale rows, and prove recomputation restores a fresh result on local
and fake-cloud backends. Unit tests cover all bulk-copy sources, physical plate
order, fallback groups, and incomplete layouts. The Growth UI workflow checks
that both actions remain available.
