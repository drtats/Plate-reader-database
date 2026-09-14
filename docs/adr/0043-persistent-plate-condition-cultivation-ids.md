# ADR 0043: Persist separate plate and condition cultivation numbers

Status: accepted, 2026-09-14. Supersedes the recommended naming/default workflow in
ADRs 0036–0042 while retaining their legacy export and saved-ID compatibility paths.

The user's registry-style ID must combine team, strain, cultivation system,
cultivation run number and R replicate. Each experiment is one physical plate and owns a unique number.
They define a cultivation condition group within a plate as a biological replicate
group, and the registry's R number as technical replication. They chose two digits
for the plate and two for the condition, accepting four rather than three digits.

We use `ST-EXP-MG1655-MP96A0101R1`: experiment 01, condition 01, technical replicate 1.
Condition groups never cross plates. Plate numbers use chronological initial planning,
condition groups follow physical well order, and persisted numbers are reserved.
The plate part expands beyond 99; groups stay within the 96-well capacity. Cultivation
experiment metadata includes accurate per-strain ranges of combined condition codes.

Each well also has its immutable internal database ID and a readable local EXP01-A01
label. Internal IDs exist even for controls or incomplete strain metadata. Eligible
wells receive a required-style external ID. Original replicate labels and doses remain
unchanged; registry Replicate and explicit TechnicalReplicate use the same R number.
BiologicalReplicateGroup exposes the combined plate/condition code.

A new pure domain planner produces assignments and validates saved identities. A
metadata-only application preview and explicit editor/admin save persist assignments
in existing JSON fields. Saving revalidates versions and allocation in one transaction,
records provenance and old IDs, and makes no raw-data changes. Subsequent exports reuse
saved values; selection changes do not regenerate them. Changed identity conditions
are rejected rather than silently causing reassignment. Legacy generators cannot
overwrite the persistent scheme. No migration is required.

Checks cover leading zeros, multiple plates/conditions/strains, technical R groups,
plate numbers beyond 99, range gaps, stable reservations including deleted records,
missing metadata, saved-state validation, atomic rollback and concurrency, role checks,
idempotence, historical IDs, per-observation CSV joins and UI rerun/download behavior.

User-facing controls and CSV columns call the first part the experiment number.
The existing persisted `CultivationPlateNumber` key and scheme token remain internal
compatibility details. CSV metadata exposes `CultivationExperimentNumber`; generated
local labels use `EXP01-A01`. Readers accept former `P01-A01` labels, and exports
render the experiment label without changing saved cultivation IDs or reservations.
An explicit save updates the local label only; there is no automatic database write.

Strain names in persistent cultivation IDs use underscores for whitespace and
hyphens, and `d` for `Δ`/`δ` (for example `ΔacrB MG 1-2` becomes `dacrB_MG_1_2`).
Original strain metadata and condition fingerprints are unchanged. Preview reports
the name-to-code mapping; save, ranges, validation and export use the same rule.
Nonprinting characters fail with the strain label, experiment number and well.
