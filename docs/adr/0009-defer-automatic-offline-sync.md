# ADR-0009: Defer automatic offline synchronization

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The same application can run against a local database or a remote Turso database.
Automatic bidirectional `turso.sync` would add conflict resolution for metadata,
review state, deletions, roles, and immutable raw data before a demonstrated user
need exists. Portable exports already provide explicit, checksummed transfer.

## Decision

Do not add automatic push/pull controls in version 0.1. Standalone installations
remain offline and credential-free. Exchange selected runs through the versioned,
transactional portable SQLite format, and migrate whole installations through
verified backup/restore. Reconsider `turso.sync` only after real multi-device
workflows establish ownership and conflict requirements.

## Consequences

Offline failure cannot corrupt a remote database, and users always choose what is
transferred. The product does not yet offer transparent cross-device updates.
Any future sync ADR must define immutable-raw collision rules, metadata merge
semantics, deletion precedence, role authority, retry/idempotency, and sync audit
events before implementation.

## Verification

macOS and Windows package jobs run the same domain, repository, and portable-file
compatibility suites. Portable import failure tests prove transactional rollback.
