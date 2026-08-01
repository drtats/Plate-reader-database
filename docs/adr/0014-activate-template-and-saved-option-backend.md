# ADR-0014: Activate template and saved-option backend

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

Schema v1 and the legacy behavior contract reserve `plate_templates` and
`saved_options`, but the new application had no repository or application
services for them. Consequently the shared editor could not provide reusable
Growth/MIC layouts or controlled suggestions without writing SQL in UI code.

## Decision

Add typed create/update/delete commands and repository operations for templates
and saved options. Template layouts contain every A1-H12 position, remain assay
typed, use optimistic concurrency for updates/deletes, and serialize as validated
JSON. Any authenticated role may read supporting data; only administrators may
manage it, consistent with the authorization contract. Every write is
transactional and audited in provenance.

Expose templates inside the existing shared 96-well grid/table editor rather
than adding a separate template screen. A template contains editable metadata
and custom columns only: Growth raw labels and MIC raw OD values are neither
stored nor overwritten when a template is applied.

Map saved options to canonical types (`strain`, `medium`, `treatment`, and unit
or assay-specific grouping fields) and expose them as suggestions in the shared
fill helper. Suggestions never restrict free-text entry. Administrators manage
them in a compact editor-adjacent control using the existing audited services.

## Consequences

Growth and MIC screens reuse one backend and one set of editor controls rather
than implementing independent session-only templates. The local, fake-cloud,
and future remote adapters retain one repository contract. Template application
changes staged editor state only; the existing final save/commit remains the
database boundary.
Saved values are shared where the scientific concept is shared even when the UI
label differs, such as Growth `Treatment` and MIC `Antibiotic / treatment`.

## Verification

Reusable integration tests run against local and fake-cloud connections and
cover create, update, list, delete, authorization, duplicate names, optimistic
concurrency, layout validation, option deduplication, and provenance.
Mapping and UI tests additionally prove that all 96 wells and custom columns
round-trip while imported raw labels and raw readings remain unchanged.
Saved-option tests cover role enforcement, deduplication, audit events,
cross-assay column mapping, and rendering in the real fill helper.
