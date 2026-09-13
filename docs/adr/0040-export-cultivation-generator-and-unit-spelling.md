# ADR 0040: Export cultivation generator and canonical micro-unit spelling

Status: accepted, 2026-09-13. Extends ADR 0039.

The selected-run planner already calculated cumulative R numbers, but the primary
observation Replicate column still showed the original Layout label. Users also
needed to visit the workspace to supply ID components. Different micro-unit
spellings split otherwise identical conditions.

Growth Data Export now exposes pattern, team and system controls and an explicit
Generate cultivation IDs and prepare export action. These settings affect output
only. Missing experiment numbers use the same chronological library planner as the
workspace, reading a metadata projection once. Existing saved numbers are preserved.
A pure numbering function serves both paths without creating reservations.

Observation and metadata Replicate now use the same effective cultivation number.
Observation Local replicate and metadata LocalReplicate preserve the original
Layout label. Generated and saved IDs are distinct columns. Changing the selection
or any generation setting invalidates prepared downloads. Viewers can generate
exports because the operation neither writes data nor reserves numbers.

A pure domain helper maps µ, μ and known encoding artifacts (including Œºg/mL) to
ASCII u. It is used in exported treatment and inoculum units, composite condition
text, and selected-run condition matching. It preserves case, concentration values
and scales. Saved version-1 condition fingerprints retain literal-unit validation;
only selection matching opts into normalization. Raw values and metadata JSON are
unchanged. No schema migration is needed.

Checks cover no-prior-ID generation, chronological numbers, mixed unit spellings
across plates, cumulative Replicate in both files, local label preservation,
read-only database state, settings-driven download invalidation and invalid patterns.
