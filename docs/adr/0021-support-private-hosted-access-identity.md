# ADR-0021: Support one hosted-access audit identity

- Status: accepted
- Date: 2026-08-02
- Owners: integration owner
- Extends: ADR-0006

## Context

Streamlit Community Cloud can restrict a private app by viewer email, but since
Streamlit 1.42 it does not expose the Community Cloud account email to
application code. Requiring a second Google or Microsoft OIDC configuration is
unnecessary for a single-user deployment, while cloud writes still require a
stable actor for authorization and provenance.

## Decision

Keep OIDC as the default cloud identity mode. Add an explicit `hosted` mode that
trusts the private host access gate and uses one email and role from host secret
storage. The application upserts that one identity into `users` and attributes
all actions to it. Hosted mode refuses to start without a syntactically valid
configured email.

## Consequences

Single-user private deployments can use Turso without maintaining a second OIDC
client. The app cannot distinguish multiple allowed Community Cloud viewers, so
they share one role and one audit identity. Operators must not use hosted mode
for public apps or when per-user accountability is required.

## Alternatives considered

- Require OIDC for every cloud deployment: secure and retained as the default,
  but unnecessary operational work for the private single-user case.
- Read the Community Cloud viewer email: unavailable in current Streamlit.
- Remove actor attribution: rejected because write authorization and provenance
  require an actor.

## Verification

Configuration tests reject hosted mode without an audit email. Context and UI
tests prove hosted mode connects without OIDC, creates the configured user, and
uses its role. Existing tests continue to cover OIDC and read-only downgrade.
