# ADR-0027: Make layout custom-column definitions assay-wide

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner

## Context

Custom layout columns were previously discoverable only from non-empty values in
one plate's well JSON. A column added while editing one experiment therefore did
not appear in a new or unrelated experiment. An entirely blank custom column was
also absent from Growth tabular exports because no well value carried its name.

## Decision

Store each custom column name as an assay-scoped global definition using the
existing `saved_options` supporting-data table. Growth and MIC definitions remain
separate because their editor and export contracts differ. Editors and admins may
add or remove definitions; all authenticated roles may list them. Each change is
recorded in provenance.

Every layout editor merges the applicable definitions into its staged frame.
Values remain per well and per experiment in existing `custom_json` fields. Removing
a definition stops adding it to unrelated layouts but does not erase values already
saved in experiments.

Growth tabular exports append the stable, case-insensitively sorted union of
registered column names and custom names found in selected wells. Registered
columns are emitted even if every selected value is blank. Structured compatibility
keys already represented by fixed columns are not duplicated.

## Consequences

- Adding a column once makes it available in every layout for that assay.
- Column definitions require no schema migration and are included in complete
  database backup/export through `saved_options`.
- Growth observation and metadata CSV files carry the same appended custom-column
  headers, with blank cells for experiments that have no value.
- Removing a definition is non-destructive; reopening an experiment with retained
  values can still reveal that column.

## Verification

- Service integration tests cover authorization, assay separation, deduplication,
  deletion, and provenance.
- Editor unit tests cover merging definitions without overwriting values.
- Growth export unit and repository integration tests cover populated and entirely
  blank universal columns in both CSV files.
