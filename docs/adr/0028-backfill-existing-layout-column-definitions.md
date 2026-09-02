# ADR-0028: Backfill pre-registry layout-column definitions

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner
- Extends: ADR-0027

## Context

ADR-0027 made newly added custom layout columns assay-wide by registering their
names in `saved_options`. Columns saved in well `custom_json` before that change
had no registry row, so they remained visible only in experiments carrying a
value. An entirely read-time discovery mechanism would make deleting a global
definition ineffective because retained historical values would immediately
recreate it.

## Decision

Add append-only migration `0003_register_existing_layout_columns.sql`. It scans
non-deleted Growth and MIC wells once and registers previously stored custom keys
under the assay-specific layout-column option type. Reserved editor, structured
compatibility, and export names are excluded. The migration does not change any
well values or raw observations.

New columns continue to use the explicit registration service from ADR-0027.
Removing a definition therefore remains non-destructive and does not get undone
by ongoing automatic discovery.

## Consequences

- Existing custom columns with at least one saved well value become universal
  after migration 0003 is applied; users do not need to recreate them.
- A historical column that was never saved with any value cannot be recovered
  because its name was never persisted.
- Complete backups use database schema version 3. Version-1 portable run exports
  still remove post-v1 internal migration records and remain compatible.

## Verification

- Migration tests stage a version-2 database with a pre-existing custom column,
  apply version 3, and verify the new assay-wide definition.
- Reserved internal fields are verified not to become user-facing columns.
- The complete repository suite continues to cover migration idempotency,
  portable export/import, and backup/restore compatibility.
