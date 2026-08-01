# ADR-0008: Extend frozen contracts additively for the MIC module

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The Phase 1 command contract reserved MIC import and calculation, while the full
Phase 7 workflow also needs typed metadata editing, well-layout editing, review,
lock, deletion, and result-search operations. The Phase 2 `MicWell` model also
needs notes and arbitrary label pairs so legacy data can migrate without loss.
Neither addition changes endpoint calculation behavior, but both touch files in
the frozen manifests.

## Decision

Add MIC-specific command/query records to the application contract. Extend
`MicWell` with optional notes and a stable, sorted tuple of custom string labels,
and add a strict long-format CSV parser. Preserve the existing
`mic-endpoint/1.0.0` algorithm identifier because inputs, background correction,
growth calls, grouping dimensions, MIC operators, and warning behavior are
unchanged.

Record the amended file hashes in the Phase 1 and Phase 2 freeze manifests. This
is an additive contract amendment, not a replacement freeze. Future scientific
behavior changes still require a new algorithm version and a separate ADR.

## Consequences

The UI and importers can use typed operations instead of dictionaries, and
legacy notes/custom labels survive round trips. Callers compiled against the
earlier records continue to work because existing types and required fields are
unchanged. The manifests retain an auditable reason for every changed frozen
file.

## Verification

Contract hash tests cover the amended manifests. Domain tests prove strict CSV
validation and deterministic custom-label ordering. Golden MIC endpoint tests
remain unchanged, proving the scientific result contract did not move.
