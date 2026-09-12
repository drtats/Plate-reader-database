# ADR 0034: Persist cultivation identities and export linked registry metadata

Status: accepted, 2026-09-12.

## Decision

Growth Data Export produces observation rows linked by `Cultivation ID`
to one metadata row per well/cultivation. The registry column names follow the
provided laboratory example. Existing observation fields, custom layout columns,
run context, filenames, and raw/background/corrected value separation remain available.

Cultivation identity is `Team-EXP-Strain-SystemNNNRreplicate`, for example
`PN-EXP-11_J3-BRV002R1`. Run numbers have at least three digits; strain underscores
and system digits are preserved. Run numbers and biological replicates are explicit
scientific metadata, never inferred from plate position or display labels.

A generator in the Growth workspace Metadata tab previews and explicitly saves
identities using the saved layout strain and replicate. Shared descriptive defaults
are stored under `plate_custom_json.cultivation_registry`; per-well identity fields
are merged into existing well custom JSON. The plate is the registry scope because
physical plates within an experiment may use different systems and conditions.
Existing portable files already preserve these JSON fields. No migration or frozen
repository-port change is required.

Saves use editor/admin authorization, optimistic concurrency, one transaction, and
provenance. They preserve unrelated metadata and raw observations. Export is read-only:
it never allocates or changes an identity. Duplicate IDs across selected wells/runs
and saved IDs inconsistent with their current strain/replicate/components are rejected.
Unassigned historical wells retain their observations and have empty registry links,
with an explicit warning to complete metadata before registry submission.

Metadata combines per-well overrides with saved shared descriptive defaults. Unknown
facts stay empty; experiment names are not substituted for scientific objectives or
registry experiment identifiers. Raw OD, background mean OD, and background-subtracted OD
are separate columns; treatments, concentrations and units are also separate, without
a composite registry-link condition column. `Culture_Age_h` uses source measurement time minus
explicit inoculation time when both exist; otherwise it retains the existing elapsed
hours plus recorded culture-age offset convention.

## Verification

Generator examples and failures, linked multi-run CSVs, duplicate/stale identities,
metadata preservation, raw immutability, transaction/concurrency/authorization checks,
UI preview/save and export smoke, plus the repository formatting/lint/type/test suite.
