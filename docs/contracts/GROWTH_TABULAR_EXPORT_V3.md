# Growth tabular export contract v3

Status: accepted, 2026-09-12. Replaces v2 (ADR 0034).

Encoding, deterministic ordering, filenames, raw-value handling and background/QC
semantics remain as documented in v2. Export is read-only and supports viewers.

The observation CSV starts with `Cultivation ID` and `Culture_Age_h`, followed by
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
Cultivation,Local_Cultivation_ID,InoculationDateTime,ProgramMetric,CultivationExperiment,Comment,Team_Code,Strain,Strain/Strain_Aliases,CultivationSystemCode,CultivationRun,Replicate,Vessel_Alphabetical_ID,Vessel_Numeric_ID,Objective,Condition,Media,EquipmentMakeModel,CultivationProtocol,SampleAnalysisProtocol
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
`Cultivation`, `Team_Code`, `CultivationSystemCode`, `CultivationRun`. The generator
uses the persisted strain/replicate and explicitly supplied positive run number,
padded to at least three digits, to produce `Team-EXP-Strain-SystemNNNRreplicate`.
No numbering allocation is inferred from dates, conditions or plate position.

`Culture_Age_h` uses explicit inoculation time and source start plus observation
elapsed time when both clocks exist, otherwise recorded initial age plus elapsed
hours. Malformed or incompatible time-zone timestamps reject preparation. The legacy
`Culture Age H` keeps its v2 meaning. Local cultivation IDs fall back to microplate
identity plus zero-padded well position; vessel fields retain physical row/column.
