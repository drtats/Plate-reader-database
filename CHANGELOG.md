# Changelog

All notable changes follow semantic versioning and this file records application,
schema, portable-format, and scientific compatibility impact.

## Unreleased

- Added a separate multi-run Growth Data Export page that creates the established
  observation and metadata CSV pair with explicit raw, background, and corrected
  OD columns and visible stale/missing-background QC.
- Made Growth plotting use staged 96-well grid selections directly when Render is
  pressed, without separate Apply or Save selection actions and without database
  persistence or an implicit A1-A8 default.
- Moved the Growth reference plate out of the selection tabs into a collapsible
  viewer below the row/column shortcuts, with selectable layout-field labels.
- Restored the compact 8×12 checkbox-table format for direct Growth well selection
  while keeping changes live and removing the separate Apply step.
- Gave the wider Growth reference plate its own horizontal scroll viewport so it
  no longer moves the page and selection grid as one surface on narrow windows.
- Batched direct 96-well plot selection inside the Render form so checking wells
  stays local and causes no application rerun until plotting is requested.
- Preserved drag-across multi-well checking in the compact editor while preventing
  the Selection List synchronization from overwriting its submitted grid.
- Replaced competing single/combined Growth curve-label controls with one ordered
  field selector, and exposed concentration units to metadata filters, colors, and labels.
- Added inoculum size/unit filtering and display to Plate Comparison, plus selectable
  condition quick stats that count actual matching wells rather than replicate labels.

## 0.1.0 - 2026-08-01

- Created one modular Streamlit/Python platform for growth and MIC assays.
- Added pure, versioned scientific domain logic with frozen golden fixtures.
- Added schema v1, local `pyturso`, fake-cloud, repositories, migrations,
  immutable raw/provenance controls, and optimistic concurrency.
- Added five-step growth and MIC imports, indexed libraries/search, workspace
  editing, revisioned analysis, plots, review/lifecycle controls, and provenance.
- Added checksummed portable export/import with collision preview/remapping and
  verified complete backup/restore.
- Added read-only, fingerprinted legacy growth and MIC migration tools.
- Added reproducible macOS/Windows packaging workflow, OS-native user data,
  standalone database selection, and frozen launcher/server/backup smoke tests.
- Added operational performance budgets, upload limits, security/operations/user
  documentation, and explicit deferral of real Turso/OIDC and automatic sync.
- Prevented native PyArrow allocator crashes when the dual-view plate editor
  opens on macOS ARM.

Compatibility: schema v1; portable format v1; growth normalization/background
and MIC endpoint algorithm versions remain 1.0.0. No legacy source was modified.
