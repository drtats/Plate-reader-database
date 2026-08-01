# Administrator runbook

## Before enabling writes

1. Confirm the expected environment/storage mode in the app header.
2. Apply/check numbered migrations explicitly.
3. Create and verify a complete backup.
4. Confirm the first admin, editor, and viewer accounts against the role matrix.
5. Import and export a synthetic growth run and MIC plate.
6. Record database size and Turso usage before real data enters the system.

Fake-cloud mode is for development only. Production must fail closed if it lacks
authenticated identity, a registered active database user, or required Turso
configuration.

## Routine operations

- Daily when actively used: inspect application errors/diagnostic IDs and failed
  imports; confirm no quota warning.
- Before releases or migration batches: stop writes, make a verified complete
  backup, record hashes/counts, and rehearse restoration to a new database.
- Monthly: record storage, rows read/written, backup inventory, inactive users,
  and lifecycle/deletion requests.
- At 3 GB or 500 full growth runs: forecast the next year and choose paid storage,
  validated archival, or a reviewed compact-storage migration.

## Incident response

Set `PLATE_READER_WRITES_ENABLED=false` and restart to constrain the app actor to
viewer behavior. Preserve logs and diagnostic IDs without copying raw data or
credentials into tickets. If reads are trustworthy, export critical plates and
create a complete backup. Restore only into a new isolated database, verify all
logical hashes, then sample growth curves, layouts, MIC results, roles, and
provenance before switching configuration.

Never bypass an import validation, derived-result discrepancy, or migration
restore-point requirement merely to complete a cutover.

## Release checklist

- clean locked install; Ruff, mypy, full Pytest/coverage, and secret scan green;
- migration checksums and contract freeze hashes green;
- macOS/Windows package matrix and frozen smoke green for a desktop release;
- real remote contract/auth/backup drill green for a hosted release;
- changelog and compatibility notes updated;
- known limitations and rollback owner recorded.
