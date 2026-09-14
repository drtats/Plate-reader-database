# Growth tabular export contract v3

Status: accepted, updated 2026-09-14. Replaces v2 (ADR 0034).

The current default is the **persistent plate/condition scheme** in ADR 0043:
`ST-EXP-MG1655-MP96A0101R1`, where `0101` is plate `01` and condition group `01`.
Earlier export-only patterns described below remain legacy options. The new scheme
is not constrained to three digits: plate width expands beyond 99 while condition
width stays two digits (1–96 on a 96-well plate).

Encoding, deterministic ordering, filenames, raw-value handling and background/QC
semantics remain as documented in v2. Export is read-only and supports viewers.

The observation CSV starts with `Cultivation ID`, `Saved cultivation ID`,
`Cultivation experiment code`, `Cultivation ID pattern`, `Cultivation replicate`,
`Cultivation replicate scope`, `Cultivation condition key` and `Culture_Age_h`, followed by
all existing v2 columns. The older `Cultivation Short ID` is retained as a display
label; it is not the new registry identity. Separate `Treatment 2`, `Concentration 2`,
`Concentration unit 2` and the corresponding third-treatment fields are included.
Custom columns follow in case-insensitive alphabetical order. There is no composite
`Cultivation_Registry_Link/Condition` or ambiguous `MeasuredValue` column: use
`Raw OD`, `Background Mean OD` and `Background Subtracted OD`. Existing composite
condition columns are retained for compatibility alongside separate fields.

The metadata CSV contains one row per stored well (including wells without an ID),
with these registry columns first:

```text
Cultivation,SavedCultivation,Local_Cultivation_ID,InoculationDateTime,ProgramMetric,CultivationExperiment,Comment,Team_Code,Strain,Strain/Strain_Aliases,CultivationSystemCode,CultivationRun,Replicate,Vessel_Alphabetical_ID,Vessel_Numeric_ID,Objective,Condition,Media,EquipmentMakeModel,CultivationProtocol,SampleAnalysisProtocol,CultivationExperimentCode,CultivationIDPattern,CultivationReplicate,LocalReplicate,CultivationReplicateScope,CultivationConditionKey,CultivationConditionFields,CultivationReplicateMode,InternalCultivationID,CultivationNumberingScheme,CultivationExperimentNumber,CultivationConditionNumber,TechnicalReplicate,BiologicalReplicateGroup,CultivationConcentrationSignificantFigures
```

All v2 run-metadata columns follow, then separate treatment/concentration/unit fields
for three treatments, `Well`, `Well Metadata JSON`, `Experiment Metadata JSON`,
`Plate Metadata JSON`, and the same custom-column union as the observation file.
All JSON metadata is retained even when nested or not promoted to a dedicated column.
`Cultivation` joins to `Cultivation ID`. Missing IDs are blank with a per-run warning;
no fake ID or scientific objective is inferred. Duplicate IDs across selected wells
or IDs inconsistent with the saved identity components/current strain and replicate
reject preparation. Run ID + Well still identify unassigned wells.

Shared registry descriptions live in `plate_custom_json.cultivation_registry`;
well custom fields override shared descriptions. Saved identity fields are
`Cultivation`, `Team_Code`, `CultivationSystemCode`, `CultivationRun`, plus optional
`CultivationIDPattern` and `CultivationExperimentCode` (ADR 0036). The recommended
pattern uses a simple experiment number starting at `001` and zero-padded well
position: `PN-EXP-MG1655-001-A01-R1`. Each well's saved strain is used.
Numbers are suggested in experiment-date order for unnumbered runs, skipping saved
reservations (ADR 0037), and checked again at save.
The original format remains supported for IDs without saved pattern metadata:
`Team-EXP-Strain-SystemNNNRreplicate`, with a positive manually assigned run number.
Saved-ID export validates IDs using the pattern and components saved on that well.
Legacy IDs report their original pattern.

For `CultivationReplicateMode=condition` (ADR 0038), the R suffix uses saved
`CultivationReplicate`, independently of the Layout label. Metadata `Replicate`
and observation `Replicate` reflect this cultivation number; metadata `LocalReplicate`
and observation `Local replicate` preserve the Layout value. Both files expose the cultivation number, scope and
canonical condition key. The key is checked against current well conditions and
saved matching scope/field rules; changed conditions reject export until regeneration.
A local-label-only edit does not invalidate a condition-based ID. Legacy/local IDs
continue to validate against the Layout replicate. Full custom metadata is retained.

`Culture_Age_h` uses explicit inoculation time and source start plus observation
elapsed time when both clocks exist, otherwise recorded initial age plus elapsed
hours. Malformed or incompatible time-zone timestamps reject preparation. The legacy
`Culture Age H` keeps its v2 meaning. Local cultivation IDs fall back to microplate
identity plus zero-padded well position; vessel fields retain physical row/column.

Shared cultivation descriptions can also be patched across selected runs from the
Growth Run Library (ADR 0035). This uses the same plate registry JSON, so the next
CSV preparation reflects the saved values with the same per-well override rules.
Bulk shared metadata edits do not regenerate or alter cultivation IDs.

Per-run export numbering (ADR 0042, replacing ADR 0039's cumulative default) is
enabled by Growth Data Export's default checkbox.
The optional `assign_selected_replicates` API flag defaults false for compatibility.
When enabled, current conditions determine R1, R2, etc. independently within each
physical run/plate and independently of stored reservations. Every other run starts
over at R1; its cultivation experiment number distinguishes its IDs. Metadata `Replicate` / `CultivationReplicate` report the selected
number, as does observation `Replicate`. Observation `Local replicate` and metadata
`LocalReplicate` retain local labels.
Mode is `export_run`; both files share the newly formatted ID. Original IDs appear
in `SavedCultivation` / `Saved cultivation ID`. Original metadata JSON is not modified.
Shared metadata fills missing per-well identity components; missing required components
produce blank IDs with warnings, not dropped observations. The bundle exposes an
assignment preview and effective extra condition fields. No database writes occur.

The export page provides `ExportCultivationSettings` (ADR 0040) for output-only
pattern, team and system choices. Blank team/system values use saved components;
`pattern=None` uses saved patterns. With explicit generation settings, missing
experiment numbers are planned from one full-library metadata projection using
ADR 0037 ordering/reservations. The pure in-memory function uses the supplied views
as its numbering universe unless an `experiment_codes` mapping is supplied. No ID
must have been saved beforehand. Saved well components take precedence over shared
ones; explicit export pattern/team/system settings take precedence over both.
Missing cultivation run numbers use the suggested experiment number. Custom patterns
must include `R{replicate}`. Generation requires selected-run replicate assignment.

Observation `Local replicate` is inserted before the additional-layout fields
(`Raw label`, etc.). The existing `Replicate` column now agrees with the metadata
`Replicate` and ID suffix for both selected and saved condition-numbering modes.
Local/saved legacy mode continues to use the Layout replicate. Blank wells retain
local labels and do not receive sample cultivation numbers.

Unit columns for treatments 1–3, inoculum units, and composite condition text use
ASCII `u` for `µ`, `μ`, and known mojibake `Œº`, `Âµ`, `Î¼`. Selection matching applies
the same spelling normalization to treatment/inoculum/temperature units. Values,
scientific case and distinct scales are unchanged; `mg/mL` does not become `ug/mL`.
Original well/plate/experiment JSON remains unchanged. Saved condition fingerprint
validation uses its original literal-unit rules to avoid invalidating persisted IDs;
normalization is opt-in for the selection planner.

ADR 0041 adds optional `concentration_significant_figures` to the export command,
pure exporter and selection planner. API default `None` preserves exact matching;
the UI explicitly sends `2` by default (exact, 3 and 4 are selectable). Values must
be `None` or integers 1–12; booleans are invalid. A non-None precision requires
selection-based replicate assignment. Validation occurs before raw run loading.

Only treatment doses (slots 1–3) are rounded, using Decimal ROUND_HALF_UP with a
context independent of other calculations. For example 0.1875 and 0.19 match at two
significant figures. Unit spelling normalization, treatment matching and combination
sorting then follow the existing policy. Other numeric condition fields remain
exact. Unknown dose text is preserved literally; nonfinite doses are rejected.
The saved cultivation fingerprint path remains exact unless explicitly requested.

Four columns are appended after the observation layout block and after the metadata
JSON block, before custom columns in both files: `Concentration matching significant
figures`, `Matching concentration`, `Matching concentration 2`, `Matching concentration
3`. The first contains the precision or `exact`; the other three contain the readable
comparison doses and use their respective existing unit columns. Original dose
columns, stored JSON and raw/background measurements are unchanged. Preview includes
entered and matching dose summaries and the matching mode. Precision participates
in the prepared-download signature, so changing it hides outdated files.

ADR 0042 scopes selection-planner counters and matching counts to `(plate_id,
condition_key)`. Each run applies explicit export fields plus only its own saved
additional fields. The bundle's `effective_condition_fields` is a display union;
per-well metadata and preview report the fields actually used for that run. The
preview's matching-well count is local to the run. Saved library condition-numbering
and saved-ID validation remain unchanged. A UI signature version invalidates cached
cumulative downloads from earlier app code. Physical well order is used even when
projection row/column indices are absent. No persistence or schema change is needed.

## Persistent plate/condition IDs (ADR 0043)

Explicit metadata-only preview and atomic save assign IDs in the database. Subsequent
exports never recalculate these identities, even if legacy generation options are
passed. Per-well `CultivationNumberingScheme=plate_condition_v1` and
`CultivationReplicateMode=plate_condition` identify this mode. Plate metadata stores
its persistent plate number, shared matching rules and cultivation experiment ranges.
Per-well fields include `CultivationPlateNumber`, `CultivationConditionNumber`, combined
`CultivationRun`, `CultivationExperimentCode` (plate part), `CultivationIDPattern`,
`CultivationConditionKey`, `CultivationConditionFields`, stored
`CultivationConcentrationSignificantFigures`, `Cultivation`, and `CultivationExperiment`.
Range codes preserve leading zeros and are computed separately for each strain.
Replaced legacy IDs are retained in `PreviousCultivationIDs` and provenance.

Observation registry columns additionally include `Internal cultivation ID`,
`Local cultivation ID`, `Cultivation experiment number`, `Cultivation condition number`,
`Technical replicate`, `Biological replicate group`. Metadata registry columns add
`InternalCultivationID`, `CultivationNumberingScheme`, `CultivationExperimentNumber`,
`CultivationConditionNumber`, `TechnicalReplicate`, `BiologicalReplicateGroup`,
`CultivationConcentrationSignificantFigures`. The internal ID is the stable database
well ID; the local label is e.g. `EXP01-A01`. Every stored well and its observation rows
retain that internal link; blanks/missing-strain wells can have blank external IDs.
Registry Replicate and TechnicalReplicate equal the saved R number. The biological
replicate group is the combined plate/condition code; no cross-plate replicate ordinal
is inferred. The original Layout replicate is still independently exported.

Saved identities are checked using their stored matching policy, not current export
settings. Changed conditions, malformed components or inconsistent plate rules fail
explicitly; local-label-only changes do not invalidate an ID. Prepare and download
remain read-only. Preview/save require no raw observation reads, and saving all selected
plates is atomic with optimistic version and allocation checks. No schema migration
is introduced. Existing assigned IDs and numbers are never silently reassigned.

In this workflow one experiment equals one physical plate. Its unique number is
labelled as an experiment number in the UI and CSV. `CultivationPlateNumber` remains
the internal persisted key; exports map it to `CultivationExperimentNumber`. Former
`P01-A01` local labels are accepted on read and rendered as `EXP01-A01`, without
renumbering cultivation IDs or modifying storage during export.

Strain names in persistent cultivation IDs use underscores for whitespace and
hyphens, and `d` for `Δ`/`δ` (for example `ΔacrB MG 1-2` becomes `dacrB_MG_1_2`).
Original strain metadata and condition fingerprints are unchanged. Preview reports
the name-to-code mapping; save, ranges, validation and export use the same rule.
Nonprinting characters fail with the strain label, experiment number and well.
