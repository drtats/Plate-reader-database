# ADR-0011: Restore rich import metadata and dual-view plate editing

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The first new import wizard implemented only a small subset of the metadata and
well-layout workflows documented in `LEGACY_BEHAVIOR.md`. Growth Step 3 omitted
project, tags, user, instrument, temperature, units, manual subtraction, notes,
and source identity. MIC Step 3 omitted most culture metadata. Both Step 4
workflows replaced the established physical 8x12 editors with per-well selectors
or a blank-well dropdown. Those interfaces could not express the frozen legacy
data contract and would discard information during a new import.

## Decision

Add a `GrowthRunMetadata` command value and extend the existing well-layout
change values additively. Growth imports accept all first-class legacy metadata,
well plot/notes/group/inoculum fields, and arbitrary custom fields. MIC import
layout changes may stage a corrected raw endpoint before the initial atomic
commit and continue to preserve arbitrary custom label grids. Once committed,
raw observations remain immutable.

Use one canonical 96-row staged layout per wizard and expose it through both an
editable 8x12 physical plate and an editable full-well table. Changes applied in
either view update the canonical layout. Full-plate, row, and column fill helpers
and arbitrary custom columns operate on that same layout.

## Consequences

The import workflow again represents every field promised by the compatibility
contract. Existing callers remain valid because all command changes are
additive and optional. No schema or scientific algorithm version changes are
required: the version-1 schema already contains the first-class columns and JSON
extension fields, and MIC analysis still runs the same endpoint algorithm after
the staged pre-commit input is finalized.

## Verification

Unit tests prove row-major plate mapping, bidirectional grid/table transforms,
fill helpers, and lossless contract conversion. Integration tests assert rich
Growth metadata and well fields, MIC raw endpoint staging, and arbitrary custom
labels reach the database. Streamlit smoke tests cover both restored wizard
paths through atomic commit.
