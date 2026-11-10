# Growth tabular export v5

The Growth Data Export page produces UTF-8 observation and per-well metadata CSVs.
It uses compact metadata by default. The **Include full metadata** checkbox changes
the companion file. Programmatic callers select compact output with
`ExportGrowthTabularData(..., compact_metadata=True)` or the corresponding keyword
on `export_growth_tabular_data`; omitting it selects full metadata.

## Observation file

The first 21 columns, OD calculation, row ordering, and single-run filename follow
[v4](GROWTH_TABULAR_EXPORT_V4.md). After this prefix, any populated additional
treatment slot contributes its name, concentration and unit, in slot order.
For example, a two-drug plate adds `Treatment 2`, `Concentration 2` and
`Concentration unit 2`. Populated well custom columns follow in case-insensitive
name order. The selection determines the headers for the entire file. Each
observation receives the values for its own well. Zero is a populated dose.
User columns with arbitrary names retain those names.

## Metadata file

Both exports name the external join column `Cultivation ID`. Its value can be
empty for controls and wells without a strain. The metadata table also contains
`InternalCultivationID`, the stable database well identifier, and physical well
position. Use the external join for assigned wells.

Compact metadata starts with 15 fields:

```text
Cultivation ID,InternalCultivationID,Run ID,Experiment Name,Experiment Date,Project,Well,Strain,Media,Inoculum size,Inoculum unit,Inoculum_condition,Replicate,Blank,Display name
```

Active treatment triplets, populated scientific and layout fields, and populated
user columns follow. Scientific fields include temperature, culture volume,
inoculation time, equipment and protocols. A comment identical to the well notes
throughout the selection shares the Notes column.

Full metadata adds saved and local IDs, registry components, matching rules,
matching doses, original JSON, and declared custom columns that are empty in the
selection. Header changes for consumers of v4 include:

| Former metadata header | Current header |
| --- | --- |
| Cultivation | Cultivation ID |
| Saved cultivation ID | SavedCultivation |
| Internal cultivation ID | InternalCultivationID |
| Local cultivation ID | Local_Cultivation_ID |
| Local replicate | LocalReplicate |
| CultivationReplicate | Replicate |
| BG Group | Background group |
| Well Row / Well Column | Vessel_Alphabetical_ID / Vessel_Numeric_ID |

`SavedCultivation` describes the persisted ID when the legacy workflow generates
an ID for export only. `Local_Cultivation_ID` is the human-readable label such as
`EXP01-A01`. Full metadata retains one spelling of the registry components and
the effective matching precision. The original layout JSON remains available
for auditing.

## Condition identity

Numbered fields such as `Treatment 2`, `Drug 2`, `2nd drug`, and `treatment_2`
identify the same slot. Numbered concentration and unit fields follow the same
convention. The condition fingerprint includes each active drug, dose and unit.
An older saved fingerprint may omit a newly recognized treatment. Preview and
save can update it when the prior conditions still match. If the additional dose
splits an assigned group, the UI requires a reviewed reassignment and keeps
previous external IDs in history. Export preparation performs no migration.
