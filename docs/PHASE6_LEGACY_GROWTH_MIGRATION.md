# Phase 6 legacy growth migration

Status: complete against copied local artifacts and both local database adapters on
2026-08-01. No original legacy database was opened for writing. Real Turso remains
deferred with the rest of the Phase 5 remote gate.

## Supported inputs and safety boundary

The importer recognizes the table/column signatures of growth SQLite v4 and the
immediately preceding compatible schema. It opens each source through SQLite's
read-only URI mode, records the source byte size and SHA-256, and verifies the
file hash again before the target transaction can commit.

The default operation is a dry run. If the destination does not yet exist, the
batch command builds its comparison schema in memory and creates no destination
file. If a destination exists, dry-run mode opens it without applying migrations
and performs only duplicate lookups. `--commit` is required for all target writes.

Each source file is one transaction. A source containing multiple runs either
imports all of its nonduplicate runs or rolls back all writes on an error. Batch
files are intentionally independent so one malformed file produces an error entry
without hiding the reports for other files.

## Field mapping

| Legacy value | Version-1 destination | Rule |
| --- | --- | --- |
| `plate_meta.run_id` | `plates.legacy_run_id` | Preserved and used for collision detection |
| experiment name/date/user | experiment name/date/operator | Preserved; a missing required date blocks import rather than inventing one |
| complete `plate_meta` row | experiment and plate `custom_json` | Lossless provenance copy |
| well/display/blank/background group | well identity and first-class well columns | Position validated as a 96-well coordinate |
| `custom_json` | `wells.custom_json` | Object preserved exactly; malformed values retained under `_legacy_custom_json_raw` |
| media/strain/inoculum/treatments/replicate | `well_conditions` | Numeric values validated; invalid optional values become null and are reported |
| `time_min` | integer `elapsed_microseconds` plus per-channel index | Deterministic, exact minute conversion |
| `value_raw` | immutable `growth_measurements.value_raw` | Logical source and destination hashes must match before commit |
| stored backgrounds | immutable analysis revision | Values preserved with a legacy algorithm version and derived QC label |
| legacy provenance rows | new import provenance event details | Original rows embedded; new actor identity recorded separately |

The older source has no first-class values for `project`, `instrument`,
`temperature`, `temperature_unit`, or `manual_subtraction`. These are left at
their documented destination defaults/nulls and listed in every preview and
provenance report. They are never inferred from filenames or timestamps.

## Collision policy

Two checks run before import:

1. source SHA-256 + legacy run ID + importer version identifies an exact reimport;
2. `legacy_run_id` identifies the same logical run arriving from another file.

The conservative version-1 policy skips both. Reports distinguish
`skipped_duplicate_source` from `skipped_duplicate_run_id`. If the latter has a
different raw hash, the report highlights the mismatch. No automatic version is
created; a future versioning workflow must be explicit and reviewed.

## Commands

Dry-run one file or every `.sqlite`, `.sqlite3`, and `.db` file directly inside a
directory:

```bash
uv run python scripts/import_legacy_growth.py \
  .data/plate-reader.sqlite /path/to/copied-legacy-library \
  --backend fake-cloud --report migration-dry-run.json
```

Commit after reviewing the JSON report. The bootstrap flag is explicit and only
creates a missing local migration editor; it refuses to elevate, reactivate, or
take over an existing user:

```bash
uv run python scripts/import_legacy_growth.py \
  .data/plate-reader.sqlite /path/to/copied-legacy-library \
  --backend fake-cloud --commit --bootstrap-editor \
  --report migration-commit.json
```

For an already provisioned actor, omit `--bootstrap-editor` and pass
`--actor-id`/`--actor-email`. Use `pyturso` for the standalone local engine. Do
not point the first pilot at an original database; copy it to a staging directory.

## Verification evidence

Automated checks run the synthetic v4 database through both `pyturso` and
`fake-cloud` and verify:

- no destination artifact or target row is created by a dry run;
- forced failures and raw-hash mismatches roll back every target table;
- 96 wells, 384 measurements, and 8 background rows reconcile;
- the source and destination logical raw hash are identical;
- a fixed-seed random sample of eight well layouts and full curves matches;
- custom well JSON, conditions, plate metadata, background values, and provenance
  match;
- missing metadata, malformed optional metadata, unsupported schemas, and invalid
  time values are visible failures/warnings;
- duplicate source and duplicate run-ID cases are distinguished; and
- the source fixture stays at SHA-256
  `f964791d0c7a389010ff812119c61c7886a803f5946d78186a1d7391a931fc5a`.

A copied representative legacy test library was also rehearsed manually. Its two
files contained the same run under different file hashes:

| Source artifact | File SHA-256 | Result |
| --- | --- | --- |
| `edeedb85.sqlite` | `0f8f3337de89e577517f8357b7d963ca2ec62b1350a411fdce198334454b0456` | Imported |
| `master_db.sqlite` | `04ea300fff2fc3d02fa9af62e002ced59e2e1bd2f87c9c4c1cb864f4e654d5bb` | Skipped duplicate run ID |

The imported run reconciled to 96 wells and 5,856 measurements with identical
logical raw hash
`5c317bb6990158d812a3db710d52cee9357d77688594cc27fbb017d5e4b48663`.
The temporary target passed `PRAGMA integrity_check`; A1, D6, and H12 each had 61
time points. Both original source hashes were unchanged after dry-run and commit
rehearsals on copies. These artifacts contained no stored background rows, which
was correctly reported rather than filled in.

## Recovery

Delete only the staging target if a pilot is rejected. The importer never changes
the source. For a nonempty destination, create and verify a complete backup using
the Phase 5 backup procedure before a controlled migration batch.
