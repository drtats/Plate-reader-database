# ADR-0033: Export all Growth layout columns

- Status: accepted
- Date: 2026-09-02
- Owners: integration owner
- Extends: ADR-0027, ADR-0031

## Context

The supplied laboratory CSV establishes a useful compatibility block, but newer
Growth experiments store additional canonical and user-defined layout fields. Using
the old file as a strict whitelist would silently omit fields that did not exist in
that example.

## Decision

Preserve the supplied 29 measurement columns in their existing order, then append
explicit columns for every canonical Growth layout field not already present by the
same name. Append the complete union of assay-wide custom-column definitions and
custom fields stored in selected wells after the canonical block.

Registered custom columns remain present even when every selected value is blank.
The experiment metadata companion remains one row per selected run and does not
absorb well-level layout values.

## Consequences

- The legacy prefix remains compatible with existing consumers.
- New standard and custom layout fields are not discarded.
- Some canonical columns intentionally duplicate information represented under a
  legacy name, making the modern field names explicit and stable.
- Observation files become wider as the layout schema grows.

## Verification

- Unit tests assert that all 17 canonical Growth layout fields occur in the
  measurement schema and that their stored values are exported.
- Existing tests continue to assert populated and entirely blank universal custom
  columns after the fixed schema.
