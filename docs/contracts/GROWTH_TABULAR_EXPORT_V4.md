# Growth tabular export contract v4

Status: accepted, 2026-09-14. Supersedes the CSV column layout in
[GROWTH_TABULAR_EXPORT_V3.md](GROWTH_TABULAR_EXPORT_V3.md).

The recipient-facing export follows the user's `metadata move.xlsx` example. It
still produces two UTF-8 CSV files, with deterministic run/well/time ordering and
one metadata row per stored well. No observations or stored values are modified.

## Observation columns

The observation file has exactly these columns, in this order:

```text
Cultivation ID,Strain,Media,Inoculum size,Replicate,Cultivation Short ID,Time Min,Culture_Age_h,Background Subtracted OD,Raw OD,Background Mean OD,Background SD OD,Well Row,Well Column,Inoculum_condition,Condition 1 State,Condition 2 State,Condition 3 State,Treatment,Concentration,Concentration unit
```

`Culture_Age_h` is the only culture-age column. It uses the v3 inoculation/source
clock calculation when both timestamps exist, otherwise initial age plus elapsed
hours. `Culture Age H` is removed. OD values, the existing corrected-OD floor, and
background freshness checks are unchanged.

Other fixed fields move to metadata, including saved/internal/local IDs, experiment
and condition numbers, the matching rule and matching doses, original Layout
replicate, experiment descriptions, flags, and separate second/third treatments.
The metadata starts with the former observation's fixed fields in their previous
order, then retains the v3 metadata fields without duplicate header names. Shared
fields in the reference appear in both files. Custom columns appear only in
metadata, with `Inoculum_condition` also included at its fixed observation position.
Original well, experiment and plate JSON remain available in metadata.

The user explicitly excluded `Date Time`, `Background Blank N`, `Background QC Flag`
and `Background QC Reason` from both exports. These fields remain in the application
and are not promoted as custom columns. Missing source-clock warnings are no longer
emitted for the omitted timestamp column. Missing/stale background warnings remain
because they affect exported corrected OD. Scientific `InoculationDateTime` metadata
is retained; a date alone never supplies an invented source clock.

`Cultivation ID` joins observations to metadata `Cultivation ID` / `Cultivation`.
Metadata retains stable internal identity and well position for controls and wells
without external cultivation assignments. Its `Microplate ID` uses the assigned
experiment number, or experiment name before assignment, rather than generic
`Plate 1`. Signal type is retained in well metadata. A well with multiple signal channels
rejects this recipient export rather than producing indistinguishable measurement
rows; portable export retains all channels.

## Concentration matching and labels

The recommended setting is **two decimal places**, using Decimal half-up rounding:
384 stays 384; 0.185 and 0.1875 match 0.19. Only comparison doses are rounded. Original
concentrations, inoculum, time, and OD values remain unchanged. This rule also rounds
doses smaller than 0.005 to zero; users can choose exact matching instead.

The API adds `concentration_decimal_places`; an explicit legacy
`concentration_significant_figures` remains available to validate saved assignments.
Only one rounding mode may be requested at a time. Decimal places accept integers
0–12, significant figures 1–12; boolean precision settings and nonfinite doses
are rejected.
Metadata identifies decimal and legacy significant-figure precision separately.

New cultivation assignments persist `CultivationConcentrationDecimalPlaces`.
Existing significant-figure assignments keep their original interpretation until
an explicit preview/save upgrades the matching rules. When grouping is unchanged,
the upgrade preserves experiment numbers, condition numbers, R suffixes, and IDs.
If rounding changes group membership, the default preview stops and explains why.
An explicit reassign option produces a before/after ID preview; saving that reviewed
plan retains previous IDs in history. Experiment numbers remain stable.
Preparation/download stays read-only and validates saved fingerprints under their
stored rule. Changing rules invalidates any previously prepared download.

Micro spelling normalization also applies to generated display names and exported
saved display labels. Full unit components such as `Œºg/mL`, `µg/mL` and `μg/mL`
become `ug/mL`, including inside underscore-separated labels. The normalizer uses
the well's known units so unrelated strain names and Greek text remain intact.
Unit scales and scientific case are unchanged. Original source JSON is preserved.
