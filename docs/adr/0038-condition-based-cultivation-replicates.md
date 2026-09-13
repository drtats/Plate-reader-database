# ADR 0038: Number cultivation replicates across matching conditions

Status: accepted, 2026-09-12. ADR 0039 moves the default numbering workflow to
export selection; the saved condition-numbering mode remains available.

The required cultivation identifier retains `R{replicate}`. A local Layout replicate
label can repeat on different plates and must not be interpreted as the shared
cultivation replicate number. We retain local labels and add condition-based numbering
for the ID suffix; each well counts as one cultivation in this mode.

A metadata-only Growth well projection supports matching across plates, excluding
blanks and deleted runs from new assignments. Saved reservations on deleted runs
remain occupied. Matching includes strain, medium, treatment/concentration/unit
combinations, inoculum size/unit, temperature/unit, culture volume, and optionally
named additional well custom fields. Numeric representations are normalized; units
are explicit with no guessed conversion. Missing strain or medium prevents merging
that well with other wells. The UI explains the matching fields and shows matches.

An optional shared `CultivationReplicateScope` can be assigned to multiple runs in
the Library. Unscoped runs match other unscoped runs. Scope and additional condition
fields are descriptive grouping configuration, not changes to saved identities.

Each matching condition group receives R1, R2, etc., in experiment-date and physical
well order, across all eligible wells. Existing condition-based numbers stay reserved;
local replicate labels and legacy IDs are not treated as global reservations. Preview
is read-only. Save recomputes the allocation inside its authorized transaction and
rejects stale proposed assignments. Raw observations and Layout replicate values
remain untouched; provenance records the ID changes.

Per-well custom metadata stores `CultivationReplicate`, `CultivationConditionKey`,
`CultivationReplicateScope`, `CultivationConditionFields`, and
`CultivationReplicateMode`. The old local mode stays supported. Export validates
new IDs against their saved replicate and current conditions. Its metadata Replicate
matches the ID suffix; LocalReplicate and measurement Replicate preserve the local
label. Both CSV files expose the cultivation replicate and matching configuration.
No schema migration or automatic save on a Streamlit rerun is introduced.

Legacy primary treatment JSON remains a fallback when structured columns are null.
Growth Layout edits record `primary_condition_overrides` in condition custom JSON
when a structured value is set or a non-null value is cleared. An unchanged null
does not hide historical JSON-only metadata. Condition matching and CSV export use
the same resolver, so an explicit clear cannot resurrect an old legacy dose.
Generated identity/bookkeeping fields cannot be selected as additional conditions.

This numbering represents cultivated wells with matching recorded conditions. It
cannot establish independence of source cultures from matching metadata alone;
users choose a scope appropriate to their experimental replication design.
