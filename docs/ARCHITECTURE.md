# Target Architecture

Status: planning baseline, 2026-08-01.

This document defines the intended dependency boundaries. Exact table columns and
public APIs are finalized in Phase 1 before production code begins.

## 1. Product shape

One repository will provide three compatible operating modes:

| Mode | UI | Storage | Purpose |
| --- | --- | --- | --- |
| Cloud | Streamlit Community Cloud | Turso Cloud through the official Python `libsql` driver | Shared, always-available laboratory database |
| Local server | Streamlit on a workstation, optionally through Cloudflare Tunnel | Local database through `pyturso`, or remote Turso by configuration | Fast local use and private remote access |
| Standalone | Packaged Streamlit application | Local database through `pyturso` | Offline use and portable distribution |

Future optional mode: a local database connected to Turso with explicit
`turso.sync` push/pull. It is intentionally deferred until cloud-only and
local-only behavior are independently reliable and conflict rules are tested.

## 2. Technology baseline

- Python 3.12, pinned for local development and Streamlit deployment.
- Streamlit, pinned to a tested version.
- Pandas and NumPy for the first implementation of parsing and analysis.
- Plotly for interactive plots; Matplotlib only for explicit publication/export
  rendering when needed.
- Pydantic v2 for validated domain and application data transfer models.
- Plain numbered SQL migrations and small typed repository implementations.
- `pyturso` for local/embedded databases.
- official Python `libsql` for direct cloud access from stateless Streamlit hosting.
- `uv` plus `pyproject.toml` and `uv.lock` for reproducible environments.
- Pytest, Ruff, and mypy for automated checks.

An ORM is not part of the baseline. SQLite-compatible SQL is small enough to keep
explicit, and the supported Turso clients expose a Python DB-API-compatible
surface. This decision can be revisited through an ADR if repository code becomes
difficult to maintain.

## 3. Dependency direction

Dependencies point inward. Lower layers never depend on the UI.

```text
Streamlit UI
    |
    v
Application services and repository interfaces
    |
    +--------------------+
    v                    v
Domain models/logic   Infrastructure adapters
                         |-- local pyturso
                         |-- cloud libsql (direct remote)
                         |-- imports/exports
                         `-- filesystem/secrets/logging
```

### Presentation layer

Responsibilities:

- navigation, forms, page-scoped session state, and rendering;
- metadata-only Run Library discovery and a separate on-demand plate-comparison surface;
- converting user input into application commands;
- displaying application results and typed errors;
- UI-only caching and fragments.

Prohibited:

- SQL;
- database connection creation;
- scientific calculation logic;
- runtime schema migrations;
- silently rewriting full runs on widget reruns.

### Application layer

Responsibilities:

- use-case orchestration;
- transaction boundaries;
- authorization checks;
- idempotency and import workflows;
- repository protocols and result DTOs;
- coordinating domain calculations with persistence.

Representative services:

- `ImportGrowthRun`
- `UpdatePlateMetadata`
- `UpdateWellLayout`
- `ComputeBackgroundRevision`
- `SearchRuns`
- `ExportPortableRun`
- `ImportLegacyRun`
- `ComputeMicRevision`

### Domain layer

Responsibilities:

- plate/well identity and validation;
- growth-data normalization;
- background calculations and QC;
- MIC calculation and warning rules;
- analysis revision models;
- pure transformations with deterministic outputs.

The domain may use Pandas/NumPy internally, but it must not know whether data came
from a file, Streamlit upload, SQLite, or Turso.

### Infrastructure layer

Responsibilities:

- database connections and repositories;
- migrations and transaction implementation;
- legacy importers and portable exporters;
- secrets/configuration loading;
- structured logging and diagnostics.

Local and cloud repositories run the same repository contract tests.

## 4. Proposed repository layout

```text
Plate-reader-database/
|-- app.py
|-- pyproject.toml
|-- uv.lock
|-- README.md
|-- AGENTS.md
|-- .streamlit/
|   `-- config.toml
|-- src/plate_reader/
|   |-- domain/
|   |   |-- common/
|   |   |-- growth/
|   |   `-- mic/
|   |-- application/
|   |   |-- commands/
|   |   |-- queries/
|   |   |-- services/
|   |   `-- ports/
|   |-- infrastructure/
|   |   |-- database/
|   |   |-- importers/
|   |   |-- exporters/
|   |   `-- config/
|   `-- ui/
|       |-- pages/
|       |-- components/
|       `-- state/
|-- migrations/
|-- tests/
|   |-- unit/
|   |-- integration/
|   |-- contract/
|   |-- migration/
|   |-- ui/
|   `-- fixtures/
|-- scripts/
|-- docs/
|   `-- adr/
`-- .github/workflows/
```

Directories are created only when their phase begins. Empty scaffolding is
avoided.

## 5. Data ownership and invariants

### Shared/core records

- `experiments`: scientific context that may contain one or more plates.
- `plates`: one physical plate/run, with assay type and lifecycle status.
- `wells`: the physical plate layout and stable well identity.
- `well_conditions`: queryable common conditions such as strain, medium,
  replicate, inoculum, treatment, concentration, and unit.
- `import_sources`: filenames, hashes, parser version, and import status.
- `analysis_revisions`: calculation version, parameters, creator, and timestamp.
- `provenance_events`: append-only audit history.
- `users`: application role and active status keyed to authenticated identity.
- `schema_migrations`: applied schema versions.

### Growth module records

- `growth_series_chunks`: immutable, lossless compressed matrices used by new
  growth imports (one record per plate/channel).
- `growth_measurements`: immutable row-based time-series values retained as a
  backward-compatible read path and as the canonical portable representation.
- `growth_backgrounds`: background/QC results tied to an analysis revision.
- `growth_metrics`: optional derived AUC, lag, maximum OD, and growth-rate values
  tied to a revision.

### MIC module records

- `mic_readings`: immutable endpoint observations per well.
- `mic_results`: derived group-level MIC values and warnings tied to a revision.

### Required invariants

- Stable UUID/ULID identifiers are independent of filenames and editable names.
- One well position is unique within a plate.
- Growth measurements are unique by plate, well, channel, and time index.
- Store both an integer time index and elapsed time; never depend on floating-point
  time equality as the only identity.
- Raw measurements are never modified by background subtraction.
- Re-importing the same source hash is idempotent unless the user explicitly
  creates a new version.
- Metadata edits update metadata only; they never rewrite measurement rows.
- Derived results record algorithm version and parameters.
- Multi-table writes are transactional.
- Deletion is soft by default and appears in provenance.

## 6. Portable import/export contract

The first portable format remains a schema-versioned SQLite-compatible database
file because it is easy to inspect, archive, upload, and migrate from Python.
Phase 1 must verify that a `pyturso`-produced file can be opened by standard
`sqlite3`. If it cannot, the portable exporter will deliberately write a standard
SQLite file independently of the application's active database driver.

Every export must contain:

- schema version;
- experiment, plate, well, and condition records;
- raw observations;
- selected analysis revisions and provenance;
- source hashes and export timestamp;
- a manifest/checksum record.

Import behavior:

1. Validate the file before writing.
2. Produce a preview and collision report.
3. Import in one transaction.
4. Preserve source identifiers where safe; remap with a recorded mapping where
   collisions require it.
5. Generate a verification report with record counts and hashes.

Legacy growth and MIC databases are accepted through explicit versioned importers,
not through conditionals scattered across repositories or UI code.

## 7. Configuration and secrets

One typed configuration object selects storage mode:

- `local`: local `pyturso` database path;
- `cloud`: `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`;
- `fake-cloud`: a local, isolated adapter used during development until the real
  Turso project is configured;
- `sync`: deferred `turso.sync` configuration.

Rules:

- environment variables or Streamlit secrets supply credentials;
- `.streamlit/secrets.toml` is ignored and never committed;
- application startup checks connectivity and schema compatibility;
- migrations run through an explicit command or deployment step;
- the UI displays a non-secret environment indicator.

`fake-cloud` must implement the same repository ports and failure semantics used
by cloud application services. It is a test/development mode, not a claim that
network behavior has been verified. Production cloud readiness still requires the
remote contract and failure checks in Phase 5.

## 8. Authentication and authorization

Cloud deployment supports two explicit identity modes. OIDC mode uses Streamlit
OIDC (`st.login`, `st.user`, `st.logout`) with Google or Microsoft; the
application `users` table supplies per-user roles. Hosted-access mode relies on
the private Streamlit Community Cloud email gate and uses one secret-configured
audit identity because Community Cloud does not expose viewer emails to the app.
Hosted-access mode is limited to deployments where shared attribution is
acceptable; its configured role applies to every allowed viewer.

- Anonymous users cannot reach data pages: OIDC enforces this inside the app,
  while hosted-access mode requires a private host-level access list.
- Viewers cannot write.
- Editors can create and edit but cannot manage users or destructive operations.
- Admins manage roles, soft deletion, restores, and migrations.
- Every committed write records the authenticated actor.
- Local development uses an explicit development identity; it cannot be enabled
  accidentally in cloud mode.

## 9. UI direction

The UI follows the experimental workflow instead of exposing storage mechanics.

### Run Library

- searchable/filterable run list;
- clear assay/status/date/user columns;
- create/import action;
- no full measurement download to populate the list.

### New growth run wizard

1. Select/upload source files.
2. Preview parser results and detected time/well structure.
3. Edit plate metadata and layout.
4. Configure blanks/background groups and inspect QC.
5. Review record counts and commit once.

Incomplete work remains a draft in session or an explicit draft record; it is not
hidden inside a partially overwritten production run.

### Run workspace

- persistent header with identity, status, source, and unsaved-change indicator;
- Overview and QC;
- Layout and metadata;
- Plotting;
- Analysis revisions;
- Export and provenance.

Use Streamlit forms and fragments to limit reruns. Expensive plots render lazily.
The 96-well overview should use efficient interactive traces or a heatmap/selector,
not regenerate 96 Matplotlib axes on every widget change.

### Administration

- connectivity and migration status;
- users and roles;
- backup/export tools;
- legacy import verification reports;
- diagnostic information safe to share in bug reports.

## 10. Observability and troubleshooting

- Structured logs include request/use-case name, run/plate ID, actor ID, duration,
  and outcome without credentials or raw laboratory data.
- Domain exceptions have stable error codes and user-safe messages.
- A diagnostic page reports app/schema version, storage mode, connectivity, and
  migration status.
- Import and migration jobs retain validation and record-count reports.
- Performance-sensitive use cases expose timings in development mode.

## 11. Deferred decisions

These require measured evidence rather than early complexity:

- offloading raw measurements to Parquet/object storage;
- bidirectional local/cloud sync and conflict resolution;
- a TypeScript frontend;
- a separate Python API service;
- generalized support beyond 96-well plates;
- advanced SciPy curve fitting.

Each deferred change requires an ADR and must preserve the repository and portable
file contracts.
