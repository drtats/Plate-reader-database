# ADR 0039: Assign cultivation replicates within the export selection

Status: accepted, 2026-09-12. Updates the default workflow in ADR 0038.

The user requires the R suffix but wants replicate numbers determined when choosing
Growth Data Export runs. The default export action now assigns R1, R2, etc. to
matching wells among the selected runs only. Different export selections can have
different R numbers for the same well. Database IDs and local replicate labels
remain unchanged.

An explicit `assign_selected_replicates` command/function option preserves the
existing saved-ID export path when false. Growth Data Export enables the option by
default; callers relying on the old API retain its false default. Workspace numbering
uses local labels by default; existing saved condition-numbering settings remain
available for compatibility.

The selected-run planner uses current condition metadata and shared study scopes.
It applies one union of saved and explicitly entered additional condition fields
across the selection. Matching retains the pure domain normalization and explicit
legacy treatment-clear handling from ADR 0038. It ignores all saved replicate
reservations and numbers samples in experiment-date and physical well order.
Blanks do not join sample groups. Missing strain/medium does not merge unrelated wells.

Exported IDs use the selected replicate and current strain with saved pattern and
identity components (shared defaults are used when no per-well component exists).
The pattern must contain the replicate placeholder. Missing required components
leave an ID blank with a warning; raw observations and metadata are still exported.
The effective identity is shared by both CSVs. `SavedCultivation` in metadata and
`Saved cultivation ID` in observations retain the original saved ID for traceability.
Original well/plate/experiment JSON is preserved, not overwritten with export values.

The bundle includes a preview of each assignment and matching counts. Prepared
artifacts are associated with run selection, assignment mode, and additional-field
settings; changing any of these requires preparation again. The operation is
read-only, works for viewers, and neither reserves numbers nor writes provenance.
No schema migration is required.

ADR 0040 adds explicit generation settings on the export page, aligns the primary
observation Replicate column with the ID suffix, and normalizes micro-unit spelling.

ADR 0042 supersedes cumulative export numbering: replicates now restart within each run.
