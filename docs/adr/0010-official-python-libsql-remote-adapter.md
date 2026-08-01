# ADR 0010: Use the official Python libSQL driver for direct Turso access

- Status: accepted
- Date: 2026-08-01
- Supersedes the remote-driver name in ADR 0002; local and fake-cloud decisions
  remain unchanged.

## Decision

Use Turso's official `libsql` Python package for direct, over-the-wire access from
Streamlit Community Cloud. Keep `pyturso` for local embedded databases and retain
the isolated SQLite fake-cloud adapter for credential-free development.

The earlier name `turso_serverless` referred to the serverless role, but that
package name belongs to Turso's TypeScript SDK. Turso's current official Python
guidance identifies `libsql` for remote access. The adapter remains behind the
same DB-API-shaped connection and repository ports, so domain and application
code do not depend on the driver.

## Consequences

- `cloud` mode requires a secure `libsql://` or `https://` URL and an auth token
  supplied only through environment or Streamlit secret storage.
- Remote migrations and transactions use the same numbered SQL and repository
  boundaries as local modes.
- An opt-in live contract test requires a fresh isolated Turso test database.
- Real network behavior, quotas, backup restoration, and latency cannot be
  accepted from fake-cloud evidence.

References: [Turso Python quickstart](https://docs.turso.tech/sdk/python/quickstart)
and [Turso SDK selection](https://docs.turso.tech/sdk/introduction).
