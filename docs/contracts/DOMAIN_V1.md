# Scientific domain API v1

Status: Phase 2 stable candidate, 2026-08-01.

The implementation lives entirely under `src/plate_reader/domain`. It accepts
in-memory values, returns immutable records, and imports no Streamlit, database
driver, secrets, OS, or filesystem modules.

## Common plate identity

- Version 1 supports the standard 8x12, 96-well geometry.
- Canonical positions are `A1` through `H12` in physical row-major order.
- Parsing trims whitespace, normalizes case and leading zeroes, and rejects
  positions outside the selected geometry.
- Duplicate physical positions are errors even when their source strings differ
  (`A1`, `a01`).
- Validation failures expose stable `IssueCode` values; UI text may change without
  forcing callers to parse messages.

## Growth normalization (`growth-normalize/1.0.0`)

`parse_growth_csv(text, settings)`:

1. Reads a CSV string and recognizes `Time` case-insensitively.
2. If time exists, requires finite, nonnegative, unique values and sorts them.
3. If time is absent, generates `t0 + input_index * interval`; interval must be
   finite and positive.
4. Converts minutes once to integer elapsed microseconds and assigns sequential
   `time_index`; floating time is not an identity.
5. Anchors columns by canonical well name, independent of column order.
6. Rejects duplicate/malformed well-like columns and non-finite measurements.
7. Allows a partial plate for legacy compatibility but returns a
   `missing_wells` warning. Unknown non-well columns are ignored with a warning.

`parse_label_layout(text)` requires exactly 8 rows and 12 columns and maps cells
to physical wells row-major. Unlike the legacy loader, a malformed shape cannot
be applied partially.

## Growth background (`growth-background/1.0.0`)

For every background group, channel, and timepoint:

- mean is the arithmetic mean of raw blank values;
- SD is sample standard deviation (`n - 1`), matching pandas/legacy behavior;
- `CV = SD / max(abs(mean), 1e-9)`;
- QC is `good` below 0.05, `caution` from 0.05 to below 0.10, and `high_cv` at or
  above 0.10.

One blank produces SD/CV zero plus an `insufficient_blanks` warning. Groups with
no blanks produce no background rows and return `missing_background`. Subtraction
returns `corrected_value=None` when background is missing; this intentionally
replaces the legacy silent zero-background fallback. When available:

`corrected = raw - time-varying group mean - manual offset`

Raw observations are never mutated or clipped.

## MIC endpoint (`mic-endpoint/1.0.0`)

1. Background is the mean raw value of all wells marked blank. No blanks retains
   the legacy zero fallback but emits `missing_blanks`.
2. `corrected = max(0, raw - background)`.
3. Growth is `corrected >= threshold`; threshold must be finite/nonnegative.
4. Nonblank wells group by trimmed strain, treatment, medium, replicate, and unit.
   Missing labels become `Unknown` with a warning.
5. A nonblank well without a valid concentration is excluded with a warning.
6. At duplicate concentrations, the concentration is called growth if any well
   grows, matching legacy behavior.
7. MIC is the lowest no-growth concentration. All growth returns `> highest`; all
   no-growth returns `<= lowest`.
8. Growth above the first no-growth concentration returns the first no-growth MIC
   plus a `growth_bounce` warning.

The group key is compact canonical JSON rather than underscore concatenation, so
labels containing underscores cannot collide. `threshold_used` is set directly
on results; this fixes the legacy function's temporary `0.0` value that the UI
later patched.

## MIC plate parsing (`mic-long-csv/1.0.0`)

`parse_mic_plate_csv(text)` accepts long-form rows anchored by canonical
`well_position` and a finite `od_raw`. It recognizes documented legacy aliases,
validates booleans and positive integer replicates strictly, rejects duplicate
wells and duplicate normalized headers, and sorts wells in physical row-major
order. Columns outside the scientific schema are retained as deterministic,
sorted custom string-label pairs. Notes and custom labels are metadata only and
do not alter `mic-endpoint/1.0.0` grouping or calculations.

## Intentional differences from legacy

- Strict finite numbers, nonnegative unique time, exact label dimensions, and
  geometry validation fail before persistence.
- Missing growth background is not represented as successfully corrected raw
  data.
- Single-blank sample SD is explicitly zero with a warning instead of NaN.
- MIC grouping keys are collision-safe and the actual threshold is returned by
  the calculation itself.
- No exception is swallowed. A failure is either a typed validation error or an
  explicit warning in the result.

Golden tests prove compatibility for unchanged behavior and assert every change
listed above. Scientific changes after this API is frozen require a new algorithm
identifier and ADR; fixes to parsing error text do not.
