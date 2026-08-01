# Legacy behavior and compatibility matrix

Status: Phase 1 characterization baseline, 2026-08-01.

The two legacy folders were inspected read-only. The fixtures in
`tests/fixtures` contain synthetic data only; no laboratory data was copied. The
golden JSON files were produced by `scripts/capture_legacy_golden.py`, which calls
the unmodified legacy analysis functions.

## Growth v4

### Workflows

| Workflow | Legacy behavior | Version-1 contract |
| --- | --- | --- |
| New run | Upload `OD.csv`, optionally upload an 8x12 labels CSV, or discover `OD.csv` and `temp.csv` in a local folder | Support uploads in every mode; local path discovery is an optional local-only convenience |
| Time parsing | A case-insensitive `Time` column is numerically converted and sorted | Preserve; reject nonnumeric, duplicate, negative, or non-finite time with a typed validation report |
| Missing time | Generate `t0 + row_index * interval`, defaults 0 and 10 minutes | Preserve with explicit units and parameters recorded on the import source |
| Wells | Recognize exact `A1` through `H12` columns and melt in physical plate order | Preserve case-insensitively after trimming; report missing, duplicate, and extra columns before import |
| Labels | Read headerless CSV and flatten row-major; warns rather than rejects a non-8x12 shape | Require exactly 8x12 for a 96-well plate; preview mapping before commit |
| Metadata | Edit experiment, project, tags, date, user, instrument, temperature, units, manual subtraction, source hints | Preserve all fields; searchable common values become columns and the remainder stays in `custom_json` |
| Well layout | Edit labels, blank flag, background group, plot selection, media, strain, inoculum, replicate, notes, treatment/concentration/unit, and custom columns | Preserve; common conditions become first-class fields and arbitrary labels remain lossless JSON |
| Background | At each time/channel/group, sample mean, sample SD, count, and `CV = SD / max(abs(mean), 1e-9)` over blank wells | Preserve as `growth-background/1.0.0`; add explicit QC status and missing-group warnings |
| Subtraction | Left-join a well's group background; missing background silently becomes zero | Raw values stay unchanged; missing background remains a visible warning. Plotting may display raw as a fallback but cannot label it corrected |
| Autosave | Delete and rewrite the complete run, including every raw measurement, after metadata/layout changes | Intentionally change: metadata/layout updates never rewrite raw rows; optimistic concurrency prevents lost edits |
| Run library | Scan standalone SQLite files into a master SQLite database and prune missing paths | Replace with indexed queries against the selected repository; standalone exports remain importable |
| Plotting | Plate selection, background toggle, overview, multi-curve plot, log scale, PNG/PDF downloads | Preserve after the local vertical slice; plotting reads DTOs and performs no SQL |
| Export | Standalone per-run SQLite plus older folder/CSV and DuckDB remnants | Import all identified legacy variants; version-1 export is a standard SQLite portable database |

### Growth data fields

- Plate metadata observed: `run_id`, `experiment_name`, `project`, `tags`,
  `experiment_date`, `user`/`user_name`, `instrument`, `temperature`, units,
  `bg_subtraction`, channels, source folder name/path, source hashes,
  `source_fingerprint`, `meta_hash`, app/schema version, and created time.
- Well metadata observed: well/row/column, raw/display label, blank flag,
  background group, plot flag, grouping label, medium, strain, inoculum size,
  replicate, notes, treatment, concentration, unit, per-well time offset, and
  user-added columns serialized in `custom_json`.
- Raw measurement identity observed: run, well, floating-point minutes, signal,
  and value. Version 1 adds integer `time_index` and `elapsed_microseconds` so floating
  equality is never the primary identity.
- Background fields observed: group, time, channel, mean, sample SD, CV, and blank
  count. Version 1 ties each row to an immutable analysis revision.

### Growth quirks intentionally not preserved

- Database setup and full-run writes inside Streamlit reruns.
- Silent exception handling, silent zero-background correction, and partial label
  acceptance.
- Destructive replace-on-save behavior and measurement tables without keys.
- Local absolute paths as record identity or required provenance.
- Conflicting README terminology (`DuckDB`) for the actually active SQLite path.

## MIC legacy app

### Workflows

| Workflow | Legacy behavior | Version-1 contract |
| --- | --- | --- |
| Plate entry | Seven editable 8x12 grids: raw OD, strain, antibiotic, concentration, medium, replicate, blank; arbitrary extra grids | Preserve with paste/import preview, reusable templates, and validated cell types |
| Background | Arithmetic mean of all blank endpoint ODs; zero if no blanks | Preserve as `mic-endpoint/1.0.0`, but emit a missing-blank warning when zero is a fallback |
| Subtraction | `max(0, raw - background)` | Preserve in a derived per-revision call table; raw endpoints remain immutable |
| Growth call | Background-subtracted value is growth when `value >= threshold` | Preserve and record threshold on the analysis revision |
| Grouping | Trim string labels; default missing strain/antibiotic/medium to `Unknown`; default replicate to 1 | Preserve for compatibility, while import preview flags missing grouping metadata |
| Duplicate concentration | A concentration is growth if any replicate well at that concentration grows | Preserve and document as a conservative rule |
| Normal MIC | Lowest concentration with no growth | Preserve |
| All growth | Highest tested concentration with operator `>` | Preserve |
| All no-growth | Lowest tested concentration with operator `<=` | Preserve |
| Bounce | Return first no-growth concentration and warn if growth appears at a higher concentration | Preserve warning and expose it in search/results |
| Library | List, load, edit, lock from deletion, mark manually checked, soft-delete with one shared admin password | Preserve lock/check/delete concepts; replace shared password with authenticated roles and audited actions |
| Search | Filter joined plate/experiment/well/result fields and choose result columns | Preserve common filters using indexed queries and bounded pagination |
| Visualization | Plate heatmaps, growth maps, and MIC dot plots | Preserve after repository and domain contracts are stable |
| Persistence | Local `sqlite3` or handwritten Turso HTTP wrapper selected from Streamlit secrets | Replace with repository adapters; fake-cloud is local-only and real Turso must pass remote transaction tests |

### MIC data fields

- Experiment: date, person, reader, incubation time, inoculum OD, growth phase,
  harvest OD, doubling time, notes, and extra metadata.
- Plate: name, format, threshold/method, background method, created time,
  soft-deleted, deletion-locked, and manually-checked flags.
- Well: position/coordinates, raw and corrected OD, blank, strain, antibiotic,
  concentration/unit, medium, replicate, growth call, notes, and arbitrary labels.
- Result: group identity, strain, antibiotic, medium, replicate, MIC
  value/operator/unit, threshold, concentration range/list, point count, status,
  and bounce warning.
- Saved options and plate templates are retained as supporting records.

### MIC quirks intentionally not preserved

- `INSERT OR REPLACE`, which can silently delete/recreate records.
- Replacing all wells/results during an edit and mutating raw endpoint values.
- A remote “batch” that omits `BEGIN`/`COMMIT` yet is treated as transactional.
- Catch-all exceptions that silently discard MIC groups or schema failures.
- A shared admin password and writes with no actor/audit identity.
- Experiment identifiers derived from editable plate name and date.

## Golden fixture coverage

| Rule | Evidence |
| --- | --- |
| Explicit and generated growth time | `growth_normalized_with_time.json`, `growth_normalized_without_time.json` |
| Row-major 96-well mapping | Both normalized growth goldens and `labels.csv` |
| Valid and high-CV blanks | `growth_backgrounds.json` |
| Missing background group fallback | `growth_background_edge_cases.json` |
| Normal, all-growth, all-no-growth, and bounce MIC | `mic_endpoint.json` |
| Legacy database shapes | `legacy/growth_v4.sqlite`, `legacy/mic_legacy.sqlite` |

Any intentional scientific behavior change requires a new algorithm version and
an ADR; compatibility cannot be changed silently in UI code.
