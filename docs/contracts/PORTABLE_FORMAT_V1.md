# Portable database contract v1

The portable artifact is one standard SQLite file with suffix
`.plate-reader.sqlite`. It must open with Python's standard `sqlite3` module,
independent of the active local or cloud driver.

## Required contents

- `portable_manifest`: exactly one row containing format version, schema version,
  export ID/time, exporter version, selected plate/revision IDs, and the logical
  content hash algorithm.
- `portable_table_checksums`: one row per exported table with row count and a
  SHA-256 hash of canonical rows ordered by primary key. Manifest tables are
  excluded from their own checksum.
- The version-1 schema rows needed by selected experiments, plates, wells,
  conditions, raw observations, selected analysis revisions/results, import
  sources, users referenced as actors, and provenance.

The final file SHA-256 is reported alongside the download and in the import
verification report. It cannot be embedded inside the file without changing the
file itself.

## Canonical hashing

1. Select explicit columns in schema order; never use `SELECT *`.
2. Order rows by the documented primary key.
3. Encode each row as compact UTF-8 JSON with sorted object keys, JSON `null`, and
   finite decimal values only.
4. Feed each encoded row plus `\n` to SHA-256.
5. Verify table counts and hashes before any destination write.

## Import protocol

1. Copy the uploaded bytes to an isolated temporary file and compute file hash.
2. Open read-only; reject non-SQLite files, unknown format versions, failed
   `PRAGMA integrity_check`, missing tables, extra executable schema objects, or
   checksum mismatches.
3. Produce a preview with entity counts, source IDs, collisions, identifier
   remaps, and authorization impact.
4. On confirmation, import every selected entity in one transaction using an
   idempotency key based on file hash plus manifest export ID.
5. Preserve identifiers when absent at the destination. On collision with
   different content, generate new identifiers and record the complete mapping in
   provenance. Same-ID/same-content rows are idempotent.
6. Imported users are inactive attribution records only; imports cannot grant
   roles.
7. Return a verification report containing source/destination counts, logical
   hashes, remaps, warnings, and committed provenance event.

Legacy growth and MIC SQLite files never enter this generic path. Versioned
legacy importers first identify their schema from tables/columns—not filenames—
and map them into the same preview/transaction/report workflow.
