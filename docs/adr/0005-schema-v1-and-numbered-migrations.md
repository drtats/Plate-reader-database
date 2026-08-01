# ADR-0005: Use normalized schema v1 and immutable numbered migrations

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Growth v4 stores one run per SQLite file and rewrites raw rows during ordinary
metadata edits. The MIC app has a different schema and applies best-effort
`ALTER TABLE` statements during UI startup. Local, fake-cloud, real Turso, and
portable files need one versioned contract.

## Decision

Adopt `migrations/0001_schema_v1.sql` and the data/lifecycle rules in
`docs/contracts/SCHEMA_V1.md`. Apply ordered SQL migrations explicitly, record a
SHA-256 checksum, reject modified migration history, and never migrate during a
Streamlit page rerun. Use application-generated stable IDs and revisioned derived
analysis tables.

## Consequences

Both assays share searchable experiment/plate/well context. Metadata edits are
small and raw observations cannot be overwritten. Schema changes require a new
migration and ADR; applied files cannot be edited in place.

## Alternatives considered

- Preserve both legacy schemas: rejected because cross-assay search, roles,
  provenance, and shared repositories would remain duplicated.
- Auto-create/alter tables on startup: rejected because errors are hidden and
  concurrent deployments can observe partial upgrades.
- ORM-generated migrations: deferred because the schema and DB-API surface are
  small and explicit SQL is easier to audit across libSQL clients.

## Verification

Migration tests create from empty, rerun idempotently, reject checksum changes,
roll back invalid SQL, validate query plans, and enforce immutable raw/audit rows.
