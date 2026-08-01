# ADR-0013: Unify Growth metadata and layout editors

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Growth import retained rich metadata and used a shared 96-well/list editor, but
the saved-run workspace exposed only a reduced metadata subset and a one-well
dropdown. The import contract also derived the channel without allowing the
explicit channel metadata retained by Growth v4.

## Decision

Add an optional channel to `GrowthRunMetadata`, defaulting to the normalized
source channel when omitted. Rehydrate persisted wells into the existing shared
editor model, including custom JSON columns, and use that same dual-view editor
for saved runs. Expose all first-class Growth metadata fields in the workspace.

Keep one UI direction: the existing application shell and shared plate editor.
Do not introduce a separate legacy screen or restore destructive full-run saves.

## Verification

Contract and integration tests verify explicit channel import. Editor tests
verify persisted first-class and custom well fields. The Streamlit workflow
updates rich metadata, saves the full layout, confirms raw measurement counts are
unchanged, renders plots, and round-trips a portable export.
