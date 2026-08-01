# ADR-0007: Use a self-describing standard SQLite portable format

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Users must move data between standalone, local, and cloud installations and keep
long-term inspectable archives. Legacy folders, per-run SQLite, DuckDB remnants,
and cloud databases cannot be treated as one unversioned format.

## Decision

Export a standard SQLite `.plate-reader.sqlite` file following
`docs/contracts/PORTABLE_FORMAT_V1.md`. Include a manifest, canonical per-table
counts and hashes, selected raw/derived/provenance records, and schema version.
Validate and preview completely before a single transactional import. Handle
legacy databases through separate schema-fingerprint importers.

## Consequences

Exports remain inspectable with standard tools and independent of Turso. Logical
checksums detect corruption and collision reports make ID remapping explicit.
The exporter must write a deliberate portable copy rather than exposing the live
database file.

## Alternatives considered

- CSV bundle: rejected as the primary format because relationships, types,
  revisions, and transactional validation are weaker.
- Raw active database download: rejected because it can expose unrelated users,
  deleted records, and implementation state.
- Driver-specific libSQL file: rejected unless standard `sqlite3` compatibility
  is independently verified.

## Verification

Phase 3 performs byte-file and logical round trips, checksum corruption tests,
collision/remap tests, rollback injection, and standard `sqlite3` opening.
