# ADR-0012: Complete backend parity before further UI work

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

The legacy-feature restoration mixed backend data-preservation work with an
unapproved change back to the legacy UI pattern. This made it difficult to
review functionality and caused the visual workflow to change direction.
Separately, rich fields could be imported but not all of them could be updated
and reloaded afterward.

## Decision

Keep the additive backend contracts from ADR-0011, but supersede its UI
decision. Further UI work is deferred until backend parity is verified. The
existing application UI remains unchanged during this backend pass.

Expand Growth and MIC metadata update commands so every first-class legacy
metadata field can be edited after import. Add atomic tag replacement, expose
experiment and plate JSON separately when loading a plate, and persist all
Growth well-layout fields through the update service. Raw measurements remain
immutable after commit.

## Consequences

Import, update, reload, and portable export now use the same lossless data
model. UI components can be added later without inventing storage behavior or
silently dropping fields. The project will keep one UI direction when that work
resumes; legacy applications remain feature references, not UI templates.

## Verification

Integration tests update every rich Growth and MIC metadata category, replace
tags, edit extended Growth well fields, reload snapshots, verify raw hashes are
unchanged, and inspect the exported portable SQLite database.
