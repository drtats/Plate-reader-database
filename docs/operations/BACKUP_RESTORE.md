# Backup and restore procedure

## Create a backup

Stop writes or enable read-only rollback mode, then run:

```bash
uv run --no-sync python scripts/backup_database.py \
  .data/plate-reader.sqlite backups/plate-reader-YYYYMMDD.sqlite \
  --backend fake-cloud
```

The logical backup uses standard SQLite, recreates schema v1 from migrations,
copies all 17 application tables, runs `PRAGMA integrity_check`, and refuses to
overwrite an existing destination.

## Restore drill

Restore only to a new path:

```bash
uv run --no-sync python scripts/restore_database.py \
  backups/plate-reader-YYYYMMDD.sqlite \
  restores/plate-reader-YYYYMMDD.sqlite
```

Restore validates the backup schema and executable objects, copies all records in
one transaction, compares every table's logical row count and SHA-256, runs an
integrity check, and deletes a partial destination on failure. File-level hashes
may differ because SQLite page layout and migration timestamps differ; logical
table hashes are the acceptance criterion.

## Verified fake-cloud drill

On 2026-08-01 a database containing one 13,920-row growth run was backed up and
restored:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| source | 6,283,264 B | operational source; not retained in the repository |
| backup | 5,640,192 B | `516fe5a7f55487919caa5fc3a8b959950a0572472f88a035195926d16da8c0f4` |
| restored | 6,086,656 B | `f3c6ce083c5fec1df7004b4670e065378ccc6e84622841c155231417267b89b8` |

All 17 logical table hashes matched. These synthetic artifacts lived in the
temporary directory and were not committed.

## Real Turso procedure (implemented; live drill pending)

With `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` present only in the process
environment, create a complete standard-SQLite backup. Reads and hashes stream in
1,000-row batches so multi-gigabyte databases are not loaded into memory:

```bash
uv run --no-sync python scripts/manage_turso.py backup \
  backups/turso-complete-YYYYMMDD.sqlite
```

Create a new isolated Turso restore-test database, replace the two environment
values with that database's credentials, migrate it, and restore:

```bash
uv run --no-sync python scripts/manage_turso.py migrate
uv run --no-sync python scripts/manage_turso.py restore \
  backups/turso-complete-YYYYMMDD.sqlite --confirm-empty-target
uv run --no-sync python scripts/manage_turso.py status
```

Restore rejects any target containing application rows and verifies every table's
row count and canonical SHA-256 inside the transaction. The real cloud drill and
recorded evidence remain required before the Phase 5 exit gate can pass.
