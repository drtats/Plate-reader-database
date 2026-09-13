# ADR 0036: Choose per-well cultivation ID patterns with automatic run codes

Status: accepted, 2026-09-12.

Cultivation IDs identify wells, not experiments. A run can contain multiple strains,
conditions and biological replicates; shared descriptions remain plate defaults.
The existing well-level storage and separate exported metadata rows are retained.

The default pattern is `{team}-EXP-{strain}-{experiment}-{well}-R{replicate}`.
The recommended experiment number starts at `001`, with later numbers suggested
as one above the highest numeric code currently saved in this database. The
metadata-only `growth_cultivation_codes` repository projection reads shared registry
and well custom fields for all Growth plates, including soft-deleted ones. Preview
is read-only. Save rechecks numeric reservations under `BEGIN IMMEDIATE` and rejects
a number used by another plate, including a stale suggestion from another session.
The same plate may reuse its saved number. Numbers are zero-padded to at least three
digits; users may edit them or use nonnumeric codes for custom conventions.
Numbers are local to the database; separately maintained databases can overlap.
No schema migration or persistent sequence table is introduced. A code stays
reserved while referenced by shared or per-well metadata; deleting all references
can make it available again. Dates remain descriptive metadata and changing them
does not rename cultivations.
Well positions are zero-padded (A01) and make same-strain, same-replicate-label wells
under different conditions distinct. The UI uses each well's saved strain and
replicate, never inferring biological replication from location or strain alone.

Users choose the recommended pattern, the original manually numbered laboratory
format, or a custom pattern using the supported tokens `team`, `strain`, `system`,
`run`, `experiment`, `well`, and `replicate`. Formatting is literal substitution;
attribute access, conversions, format specifications, and unknown tokens are
rejected. Preview and explicit save remain separate actions. Duplicate final IDs
are rejected across all assigned/unassigned wells and selected export runs.

The assignment command gains optional pattern and experiment-code fields. Assigned
wells persist `CultivationIDPattern` and `CultivationExperimentCode` alongside their
existing ID/components. Shared settings are defaults, not live links that rename
saved well IDs. Legacy assignments without pattern metadata retain the original
`Team-EXP-Strain-SystemNNNRreplicate` behavior and legacy IDs export unchanged.
Regenerating in legacy mode removes obsolete per-well pattern settings. No schema
migration is required; portable JSON preservation already supports these fields.

Export validation uses the pattern/code saved on each well and the current saved
strain/replicate/position. Both files retain the pattern and experiment/run code as
explicit columns. Raw measurements, background results and other metadata remain
unchanged. Tests cover mixed strains, same-strain replicates, different wells with
repeated replicate labels, sequential reservations, stable saved codes, invalid templates,
legacy compatibility, stale IDs, duplicate outputs, save/reload and export joins.
