# ADR-0006: Use OIDC identity plus database-backed roles

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The legacy MIC app uses a shared admin password only for deletion, and neither
legacy app attributes normal writes to a verified identity. A publicly reachable
Streamlit deployment needs authentication and authorization.

## Decision

Use Streamlit OIDC for cloud identity and map the verified email to an active
`users` record with viewer, editor, or admin role. Pass an explicit actor through
every command and append provenance in the same transaction. Use a conspicuous
development actor only outside production.

## Consequences

Authorization is testable outside Streamlit, role changes take effect without
changing identity-provider configuration, and all writes are attributable. The
deployment requires OIDC secret configuration before Phase 5 exits.

## Alternatives considered

- Shared application password: rejected because it does not identify actors and
  is difficult to rotate safely.
- Email allowlist only: rejected because it cannot express read-only versus admin
  permissions.
- Authorization only in UI widgets: rejected because hidden controls are not a
  security boundary.

## Verification

Phase 5 tests anonymous denial, inactive users, every role/capability pair, direct
service calls, secret redaction, and actor provenance against real cloud mode.
