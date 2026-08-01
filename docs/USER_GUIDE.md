# User guide

## Choose a mode

- `make run-local` starts the no-credential fake-cloud development app.
- `make run-standalone` starts the source-based offline app with local `pyturso`.
- A packaged `PlateReaderDatabase` executable creates its database outside the
  bundle; see the standalone guide.
- Hosted Turso mode remains disabled until its credentials and authentication are
  configured and verified.

## Growth runs

Open **New Growth Run** and follow the five saved steps: choose a CSV, validate
time/wells, enter metadata, review the 96-well layout, and commit. A valid source
has well columns such as `A1` through `H12`; `Time` is optional when a sampling
interval is supplied. The preview reports missing wells and parser warnings.

Use **Growth Run Library** for indexed search and open one workspace. Metadata
and layout changes require an explicit save. Raw observations never change.
Background subtraction creates a versioned analysis revision. Select a bounded
set of wells before rendering curves. Export produces a checksummed standard
SQLite artifact containing the selected plate and current revisions.

## MIC plates

Open **New MIC Plate** and supply long-format CSV with `well_position` and
`od_raw`. The parser accepts documented aliases such as `well`, `od`,
`antibiotic`, and `media`; unknown columns become stable custom labels. A commit
requires exactly 96 unique wells.

Use **MIC Plate Library** or **MIC Results** for bounded, server-side search. The
workspace shows raw OD and growth-call maps, result groups, revisions, review
state, lifecycle controls, export, and provenance. Changing threshold or well
layout creates a new MIC revision and preserves raw values.

## Portable transfer

Download a portable export from either workspace. In the receiving app, open
**Import Portable Data**, upload it (or select a local path in standalone mode),
and preview first. Review table counts and collisions. **Remap incoming IDs
safely** is the normal choice when the destination already contains related
records; strict rejection is useful for controlled migrations. The commit is
transactional, never overwrites existing records, and repeated import of the
same export adds nothing.

Portable import is not a full installation restore. Use complete backup/restore
for disaster recovery.

## Roles and lifecycle

- Viewer: search, inspect, plot, preview portable data, and export.
- Editor: viewer actions plus imports, metadata/layout edits, revisions, and
  manual review state.
- Admin: editor actions plus lock, soft delete/restore, and user administration.

Deletion is soft. Locked MIC plates cannot be deleted. If an operation fails,
copy the diagnostic ID shown in the UI before contacting an administrator.
