# ADR-0004: Keep committed raw observations immutable

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Background subtraction, threshold changes, metadata edits, and later algorithm
improvements must not destroy or silently alter instrument observations.

## Decision

Treat committed raw growth and MIC observations as immutable. Store computed
backgrounds, calls, metrics, and MIC results in versioned analysis revisions with
parameters, algorithm version, actor, and provenance.

## Consequences

Analyses are reproducible and can be compared. Correcting a genuinely incorrect
source import requires a new import/version rather than an in-place raw edit.

## Alternatives considered

- Store only processed values: rejected because results cannot be reproduced.
- Overwrite processed columns whenever settings change: rejected because history
  and parameter provenance are lost.

## Verification

Repository tests assert that metadata and analysis updates leave raw row hashes
unchanged. Export/import round trips verify the same invariant.
