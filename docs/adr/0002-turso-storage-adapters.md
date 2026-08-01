# ADR-0002: Separate local, fake-cloud, and Turso storage adapters

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The application must run locally, on stateless Streamlit Community Cloud, and as
a standalone package. Real Turso credentials are not configured yet.

## Decision

Define repository ports independent of database drivers. Plan `pyturso` for local
storage, `turso_serverless` for real cloud access, and a fake-cloud adapter for
development until Phase 5. Consider `turso.sync` only after local and remote modes
are reliable and conflict rules are explicit.

## Consequences

Domain and UI development can proceed without credentials. Contract tests can be
shared, but fake-cloud success never substitutes for real remote transaction,
latency, concurrency, and failure testing.

## Alternatives considered

- Handwritten SQL-over-HTTP adapter: rejected because supported DB-API-compatible
  clients exist.
- One global direct connection used from Streamlit pages: rejected because it
  couples presentation, transactions, and deployment mode.

## Verification

Phase 3 runs repository contracts locally and against fake-cloud. Phase 5 reruns
them against an isolated real Turso test database.
