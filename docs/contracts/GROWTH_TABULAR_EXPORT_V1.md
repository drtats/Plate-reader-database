# Growth tabular export contract v1

Status: accepted, 2026-08-18.

The Growth Data Export page creates two UTF-8 CSV files for one or more explicitly
selected Growth plates. Both files use LF line endings, contain no UTF-8 byte-order
mark, and end with one newline. Run order follows the submitted stable plate-ID
selection. Wells are physical row-major (`A1` through `H12`), and observations are
then ordered by channel and integer time identity.

## `growth_runs.csv`

This file has one row per immutable Growth observation. Its exact columns are:

```text
Cultivation Short ID,Date Time,Culture Age H,Well Row,Well Column,Culture Volume uL,Condition 1 State,Condition 2 State,Condition 3 State,Background Subtracted OD,Microplate ID,Background Mean OD,Background SD OD,Background Blank N,Background QC Flag,Background QC Reason,Run ID,Project,Experiment Name,Well,Time Min,Signal Type,Raw OD,Blank,BG Group,Strain,Media,Replicate,Notes
```

The three OD fields are intentionally separate:

- `Raw OD` is the stored immutable observation.
- `Background Mean OD` is the current non-stale background matched by the well's
  background group, signal type, time index, and elapsed microseconds.
- `Background Subtracted OD` is an export compatibility value:
  `max(0.0001, Raw OD - Background Mean OD)`.

The `0.0001` floor matches the supplied laboratory analysis format. It is not
written to the database and does not change the domain calculation, stored raw
measurement, interactive plot, or analysis revision.

`Background SD OD` and `Background Blank N` come from the same matched background
row. `Background QC Flag` is false only for `good`; caution, high-CV, missing,
and stale cases are true and record a machine-readable `Background QC Reason`.
When no usable background exists, raw OD remains present while the background and
corrected OD cells are blank. A missing background is never treated as zero.

`Date Time` is source start date/time plus elapsed microseconds. It remains blank
when no stored source start time exists. `Culture Age H` is the stored initial
culture age plus elapsed hours. Condition state cells combine treatment,
concentration, and unit without changing their source values.

## `growth_runs_metadata.csv`

This file contains one `run` row followed by one `well` row per selected plate.
Its exact columns are:

```text
Metadata Level,Run ID,Project,Experiment Name,Experiment Date,User,Instrument,Temperature,Source Folder,Editable Metadata JSON,Source Metadata JSON,run_id,well,display_name,media,strain,inoculum_size,treatments,is_blank,bg_group,row,col,raw_label,plot,group,replicate,notes,treatment_1,conc_1,unit_1,t0_added_min
```

Run rows leave all well-only columns blank. Well rows repeat the run context and
populate the lower-case legacy-compatible fields. Retained editable and source
metadata are serialized as deterministic JSON objects. Unknown or unavailable
optional source values are blank; the exporter does not infer them from filenames.

## Identity and safety

`Run ID` uses `plates.legacy_run_id` when available and otherwise uses the complete
stable `plate_id`; it is never shortened. The workflow is read-only and accepts
viewer, editor, and admin roles. It does not append provenance or update source,
metadata, measurement, background, or revision rows.
