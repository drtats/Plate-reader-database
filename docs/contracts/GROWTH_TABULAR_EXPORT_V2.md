# Growth tabular export contract v2

Status: accepted, 2026-09-02.

The Growth Data Export page creates two UTF-8 CSV files for one or more explicitly
selected Growth runs. Both files use LF line endings, contain no UTF-8 byte-order
mark, and end with one newline. Run order follows the submitted stable plate-ID
selection. This version replaces the mixed run/well metadata topology in v1.

## `growth_runs.csv`

This file has one row per immutable Growth observation. Its fixed columns are:

```text
Cultivation Short ID,Date Time,Culture Age H,Well Row,Well Column,Culture Volume uL,Condition 1 State,Condition 2 State,Condition 3 State,Background Subtracted OD,Microplate ID,Background Mean OD,Background SD OD,Background Blank N,Background QC Flag,Background QC Reason,Run ID,Project,Experiment Name,Well,Time Min,Signal Type,Raw OD,Blank,BG Group,Strain,Media,Replicate,Notes
```

Custom Growth layout columns follow these fixed columns in case-insensitive
alphabetical order. The export uses the union of assay-wide column definitions and
custom fields present in the selected wells, so a registered column remains in the
file even when all selected experiments have blank values.

`Raw OD` is the stored immutable observation. `Background Mean OD` is the current
non-stale background matched by group, signal type, time index, and elapsed time.
`Background Subtracted OD` is the export compatibility value
`max(0.0001, Raw OD - Background Mean OD)`. Missing or stale backgrounds leave
derived cells blank and produce explicit QC fields rather than substituting zero.

`Time Min` is elapsed acquisition time and is always derived from stored observation
timing. `Date Time` is the optional absolute source start timestamp plus elapsed
time; it is blank when the source did not supply a start clock time.

## `growth_runs_metadata.csv`

This is a homogeneous experiment/run metadata table with exactly one row per
selected run and no well-level rows. Its fixed columns are:

```text
Run ID,Project,Experiment Name,Experiment Date,User,Instrument,Temperature,Source Folder,Editable Metadata JSON,Source Metadata JSON
```

Retained editable and source metadata are serialized as deterministic JSON objects.
Unknown or unavailable optional source values are blank; the exporter does not infer
them from filenames. Well conditions and custom layout values belong to observations
and are present in `growth_runs.csv`, not this experiment-level companion file.

## Identity and safety

`Run ID` uses `plates.legacy_run_id` when available and otherwise uses the complete
stable `plate_id`; it is never shortened. The workflow is read-only and accepts
viewer, editor, and admin roles. It does not append provenance or update source,
metadata, measurement, background, or revision rows.
