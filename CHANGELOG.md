# Changelog

All notable changes follow semantic versioning and this file records application,
schema, portable-format, and scientific compatibility impact.

## Unreleased

- Made Growth plotting use live 96-well grid or list selections directly, without
  separate Apply buttons, while keeping selection persistence as an optional save.
- Moved the Growth reference plate out of the selection tabs into a collapsible
  viewer below the row/column shortcuts, with selectable layout-field labels.

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
