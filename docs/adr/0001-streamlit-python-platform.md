# ADR-0001: Use Python and Streamlit for the initial platform

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Both legacy applications use Python and Streamlit. The immediate goals are a free
cloud deployment, a compatible local/standalone mode, and a safer backend. A full
TypeScript rewrite would delay database and migration work.

## Decision

Use Python 3.12 and Streamlit for the initial shared application. Keep the domain
and application layers independent of Streamlit so another frontend can be added
later.

## Consequences

Legacy scientific behavior can be characterized and ported with less risk. The UI
must use forms, fragments, lazy rendering, and explicit saves to control
Streamlit's rerun costs. TypeScript remains deferred.

## Alternatives considered

- React/TypeScript plus an API: smoother UI, but substantially more initial work.
- FastAPI plus server-rendered UI: preserves Python but adds a separate deployment
  service and free-tier cold starts.

## Verification

Phase 4 measures rerun behavior, import/load latency, and plotting responsiveness.
