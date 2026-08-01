# ADR-0003: Enforce layered module boundaries

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The legacy applications mix Streamlit state, SQL, filesystem access, and analysis,
which makes changes difficult to test and troubleshoot.

## Decision

Use inward dependency direction: UI calls application services; application
services use domain logic and repository ports; infrastructure implements those
ports. SQL is prohibited in UI modules, and Streamlit is prohibited in domain and
infrastructure modules.

## Consequences

More interfaces are defined up front, but domain tests become fast and local/cloud
adapters become replaceable. Parallel work is safer after interfaces freeze.

## Alternatives considered

- Feature folders containing UI, SQL, and calculations together: faster for a
  prototype but reproduces the current coupling.
- A generalized plugin framework: unnecessary before two modules are stable.

## Verification

Import-boundary tests and code review enforce the dependency rules from Phase 2.
