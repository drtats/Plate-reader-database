# Growth tabular export contract v3

Status: accepted, updated 2026-09-13. Replaces v2 (ADR 0034).

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
Cultivation,SavedCultivation,Local_Cultivation_ID,InoculationDateTime,ProgramMetric,CultivationExperiment,Comment,Team_Code,Strain,Strain/Strain_Aliases,CultivationSystemCode,CultivationRun,Replicate,Vessel_Alphabetical_ID,Vessel_Numeric_ID,Objective,Condition,Media,EquipmentMakeModel,CultivationProtocol,SampleAnalysisProtocol,CultivationExperimentCode,CultivationIDPattern,CultivationReplicate,LocalReplicate,CultivationReplicateScope,CultivationConditionKey,CultivationConditionFields,CultivationReplicateMode
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

Selection-based export (ADR 0039) is enabled by Growth Data Export's default checkbox.
The optional `assign_selected_replicates` API flag defaults false for compatibility.
When enabled, current conditions among selected runs determine R1, R2, etc., independent
of stored reservations. Metadata `Replicate` / `CultivationReplicate` report the selected
number, as does observation `Replicate`. Observation `Local replicate` and metadata
`LocalReplicate` retain local labels.
Mode is `export_selection`; both files share the newly formatted ID. Original IDs appear
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
