# Executable Implementation Plan

Status: planning baseline, 2026-08-01.

This plan favors backend correctness and compatibility before UI breadth. Every
phase ends with an explicit gate. Do not begin a dependent phase until its gate is
green or an ADR documents why the gate changed.

## Operating rules for all phases

- `main` must stay runnable and all completed-phase checks must remain green.
- Every schema or public interface change is documented before dependent parallel
  work begins.
- Real credentials and real laboratory databases never enter Git history.
- Legacy sources are read-only references.
- Each phase produces a small demonstration using anonymized fixtures.
- A failed phase can be abandoned without changing legacy applications or their
  databases.

Planned standard commands, introduced in Phase 0:

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
uv run streamlit run app.py
```

Convenience wrappers such as `make check`, `make test-unit`, `make test-db`,
`make run-local`, and `make migrate-local` should call these commands rather than
duplicate configuration.

## Phase 0 - Repository and engineering foundation

Goal: create a reproducible, safe repository before application behavior exists.

### Tasks

- [x] Initialize Git with `main` as the default branch.
- [ ] Create an empty private GitHub repository and add it as `origin`.
- [x] Add `.gitignore` for secrets, databases, uploads, exports, caches, virtual
      environments, build artifacts, and OS metadata.
- [x] Add `pyproject.toml`, `.python-version`, and `uv.lock` with pinned Python,
      Streamlit, test, lint, and type-check dependencies.
- [x] Add minimal `app.py` that displays application/version/environment status
      without connecting to a production database.
- [x] Add package skeleton only for code needed by this phase.
- [x] Add Ruff, mypy, Pytest, and coverage configuration.
- [x] Add CI for formatting, linting, typing, unit tests, and secret scanning.
- [x] Add issue/PR templates that require acceptance checks and migration impact.
- [x] Add an ADR template and record the initial decisions from
      `docs/ARCHITECTURE.md`.
- [x] Add developer commands and local setup instructions.
- [ ] Inventory the current Streamlit Community Cloud workspace, including whether
      its single private-app/auth slot is already occupied by the legacy MIC app.

### Checks

- [x] A clean clone can run `uv sync --all-groups --frozen` and the full check
      command (verified 2026-08-01: 251 passed, 1 optional remote test skipped,
      90.10% combined line/branch coverage).
- [x] The minimal Streamlit app starts locally on Python 3.12.
- [ ] CI passes on GitHub without Turso credentials.
- [x] A deliberately created local secrets file and `.sqlite` file remain ignored.
- [x] The redacting tracked-file scanner finds no credentials locally; CI also
      runs the scanner plus full-history Gitleaks.
- [ ] Streamlit Community Cloud can deploy the minimal app from GitHub.

### Exit gate

Repository setup is reproducible locally and in GitHub CI, and a no-database
Streamlit smoke deployment succeeds.

### Rollback

No legacy system is touched. Delete/recreate only this new repository if the
foundation proves unsuitable.

## Phase 1 - Contracts, legacy characterization, and schema freeze

Goal: define what must remain compatible before porting code.

### Tasks

- [x] Inventory current growth v4 workflows, fields, imports, exports, and known
      quirks as a behavior matrix.
- [x] Inventory current MIC workflows and Turso/local schema in the same format.
- [x] Create anonymized fixtures:
  - [x] 96-well growth CSV with a `Time` column;
  - [x] growth CSV without a `Time` column;
  - [x] label/layout CSV;
  - [x] background groups with valid, missing, and high-CV blanks;
  - [x] MIC plate with normal, all-growth, all-no-growth, and bounce cases;
  - [x] one legacy growth SQLite export;
  - [x] one legacy MIC SQLite export generated from synthetic data.
- [x] Generate golden expected outputs from the legacy implementations, then store
      only anonymized expected data in this repository.
- [x] Define typed command/query DTOs and repository protocols.
- [x] Finalize the version-1 table design, constraints, indexes, and lifecycle
      rules.
- [x] Define algorithm version identifiers for growth normalization, background
      subtraction, and MIC calculation.
- [x] Define portable export manifest and legacy importer contracts.
- [x] Define authorization roles and write permissions.
- [x] Record ADRs for framework, database drivers, schema, migrations, raw-data
      immutability, authentication, and export format.

### Required schema review questions

- [x] Can all metadata shown in either legacy UI be preserved without silent loss?
- [x] Which fields must be first-class searchable columns versus custom JSON?
- [x] Can a metadata-only edit avoid touching raw measurements?
- [x] Is every multi-row import transactional and idempotent?
- [x] Are time identity and well identity stable across local/cloud/export?
- [x] Can one experiment contain multiple plates and assay types?
- [x] Can an old export be imported without depending on its filename?
- [x] Can analysis results be recomputed without altering raw observations?

### Checks

- [x] Behavior matrix reviewed against both legacy apps.
- [x] Every domain rule has at least one golden example or an explicit decision to
      change behavior.
- [x] Repository contract is implementable by both local and cloud DB-API clients.
- [x] Schema creates from empty using ordered migrations.
- [x] Index/query plans are documented for run listing, run loading, measurements,
      MIC search, and visualization filters.
- [x] A sample 24-hour, 10-minute, 96-well run remains comfortably within Turso
      storage/write budgets.

### Exit gate

Schema v1, application interfaces, golden fixtures, and acceptance criteria are
frozen. Interface changes after this point require an ADR and integration-owner
approval.

Exit evidence: `docs/contracts/phase1-freeze.json` plus the contract and migration
test suites. GitHub publication remains part of the still-open external Phase 0
gate; it does not change the locally frozen content.

### Parallelism

Do not parallelize final schema and interface decisions. Parallel discovery is
safe only when one owner integrates the two legacy inventories.

## Phase 2 - Pure domain and analysis core

Goal: port and improve scientific behavior without Streamlit or database coupling.

### Tasks

- [x] Implement common plate/well identifiers and 96-well validation.
- [x] Implement growth CSV normalization and time-axis handling.
- [x] Implement label/layout parsing anchored by well position.
- [x] Implement background grouping, subtraction, CV, and QC warnings.
- [x] Implement MIC endpoint background correction, threshold calls, grouping,
      MIC operators, and bounce warnings.
- [x] Implement validated domain models and stable domain error codes.
- [x] Add algorithm/version metadata to every derived result.
- [x] Document intentional behavior changes from legacy outputs.

### Checks

- [x] Unit tests cover happy paths, empty data, malformed wells, missing time,
      NaNs, duplicate times, missing blanks, and mixed background groups.
- [x] MIC tests cover all-growth, all-no-growth, duplicate concentrations, and
      non-monotonic growth.
- [x] Golden outputs match legacy behavior except documented corrections.
- [x] Domain modules contain no `streamlit`, database-driver, secrets, or
      filesystem imports.
- [x] Tests are deterministic across repeated runs.

### Exit gate

Pure analysis APIs are stable, typed, and golden-tested.

Exit evidence: `docs/contracts/DOMAIN_V1.md`,
`docs/contracts/phase2-freeze.json`, domain purity checks, and golden/unit tests.

### Safe parallel wave A

After Phase 1 freezes shared models:

| Owner | Scope | Exclusive files |
| --- | --- | --- |
| Growth worker | Growth parsing/background/QC | `domain/growth/`, matching unit tests |
| MIC worker | MIC calculation and warnings | `domain/mic/`, matching unit tests |
| Common-model worker | Plate/well validation and shared errors | `domain/common/`, matching unit tests |
| Integration owner | Shared interfaces, review, full checks | Shared contracts and integration only |

All workers use `high` reasoning. The integration owner runs golden and full suites
after merging the wave.

## Phase 3 - Database, migrations, repositories, and portable files

Goal: prove one persistence contract against local and cloud-compatible drivers.

### Tasks

- [x] Implement ordered, explicit SQL migration runner.
- [x] Implement local `pyturso` connection factory and transaction wrapper.
- [x] Implement repository methods for experiments, plates, wells, conditions,
      measurements, analysis revisions, results, provenance, and users.
- [x] Implement a fake-cloud adapter for development that satisfies the same
      repository contracts without credentials or network access.
- [x] Add indexes finalized in Phase 1.
- [x] Implement idempotent import source hashing.
- [x] Implement incremental metadata/layout updates.
- [x] Enforce raw-data immutability in repositories and service APIs.
- [x] Implement portable run export and transactional reimport.
- [x] Implement backup/export of the complete local database.
- [x] Implement synthetic seed/demo data command.
- [x] Build repository contract tests reusable by every adapter.

### Checks

- [x] Migrations succeed from empty and are idempotent when checked again.
- [x] A forced mid-import failure rolls back every table.
- [x] Reimporting the same source hash does not duplicate a run.
- [x] Updating metadata leaves measurement row count and raw hashes unchanged.
- [x] Export then import produces matching record counts, source hashes, raw
      values, and selected analysis revisions.
- [x] A portable export opens with the Python standard-library `sqlite3` client;
      if the primary local driver format is incompatible, the dedicated exporter
      writes a standard SQLite artifact.
- [x] Foreign keys and unique constraints reject invalid data.
- [x] Query plans use expected indexes.
- [x] A 13,920-row synthetic run saves and loads within recorded local performance
      budgets; budgets are set from measured CI/development baselines rather than
      guessed thresholds.

### Exit gate

The local database and portable-file path satisfy all repository contracts and
failure tests.

### Safe parallel wave B

Parallelize only after migrations and repository protocols are frozen:

| Owner | Scope | Exclusive files |
| --- | --- | --- |
| Persistence worker | Local repositories and transaction tests | `infrastructure/database/`, `tests/contract/` |
| Import/export worker | Portable format and legacy-neutral fixtures | `infrastructure/importers/`, `infrastructure/exporters/`, round-trip tests |
| Tooling worker | Seed, diagnostics, performance harness | `scripts/`, relevant integration tests |
| Integration owner | Migrations and shared application ports | `migrations/`, shared interfaces, full integration |

Do not allow multiple workers to edit the same migration.

## Phase 4 - Local growth-curve vertical slice

Goal: deliver the first usable workflow locally before cloud complexity.

### Tasks

- [x] Implement application services for growth import, preview, commit, metadata,
      layout, background revision, search, load, and export.
- [x] Build navigation and Run Library.
- [x] Build the five-step New Growth Run wizard.
- [x] Build Run Workspace pages for overview/QC, layout/metadata, plotting,
      revisions, export, and provenance.
- [x] Use forms/fragments so unrelated widgets do not rerun expensive work.
- [x] Render expensive plots on demand and cache by raw hash plus analysis
      revision.
- [x] Add explicit save/commit indicators and unsaved-change warnings.
- [x] Provide user-safe errors with diagnostic IDs.
- [x] Add local configuration and development identity.
- [x] Replace Run Library cards with a sortable metadata table whose selection
      remains browser-local until an explicit open or compare action.
- [x] Add a separate Plate Comparison workflow with metadata-only well filtering,
      persistent multi-search selection, and raw curves rendered only on request.

### UI changes intentionally allowed

- Replace the storage-oriented Builder/Viewer split with Run Library, Import
  Wizard, and Run Workspace.
- Replace automatic full-run saves with explicit transactional commits.
- Replace always-rendered 96-axis Matplotlib overview with a fast interactive
  plate selector/heatmap and lazy detailed curves.
- Keep publication-quality static export as an explicit action.
- Make background/QC state and selected analysis revision visible at all times.

### Checks

- [x] A new user can import the standard growth fixture without entering a folder
      path.
- [x] A power user can load the same data from a configured local path.
- [x] Every wizard step validates before advancing.
- [x] Reloading a committed run preserves every metadata field.
- [x] Metadata edits do not rewrite measurements.
- [x] Background on/off plotting does not recompute raw imports.
- [x] Exported run reimports into an empty database with identical verification
      report.
- [x] Streamlit reruns do not trigger migrations or unintended writes.
- [x] Sorting and selecting Library or comparison-table rows do not submit a
      backend request until the user presses the corresponding action button.
- [x] Library search and comparison-well filtering remain metadata-only; raw
      measurements are loaded only by explicit workspace or render actions.
- [x] UI smoke tests cover navigation, import, edit, plot, and export.
- [x] Manual comparison against growth v4 is documented with screenshots and
      output hashes where meaningful.

### Exit gate

The local growth workflow reaches functional parity for core import, metadata,
background, plotting, save, resume, and export, with known differences documented.

### Parallelism

Application services and UI shell may proceed in parallel only against frozen
ports. The integration owner controls shared session-state and navigation files.

## Phase 5 - Turso Cloud and authenticated Streamlit deployment

Goal: deploy the growth vertical slice safely on free hosting.

Until the user configures Turso, execute this phase against the fake-cloud adapter
and mark real-remote checks pending. Fake-cloud success cannot satisfy the Phase 5
exit gate by itself.

### Tasks

- [ ] Create a development Turso database using the selected Turso engine.
- [x] Implement the official Python `libsql` remote connection factory behind the
      existing ports (ADR 0010 corrects the earlier TypeScript package name).
- [ ] Run the same repository contract tests against an isolated remote test DB.
- [x] Add explicit remote migration command and deployment runbook.
- [ ] Configure either private hosted access with one audit identity or Google /
      Microsoft OIDC with distinct identities through Streamlit secrets.
- [x] Implement `viewer`, `editor`, and `admin` authorization checks.
- [x] Record actor identity for writes and provenance.
- [x] Add cloud-safe connection caching and query-result caching with explicit
      invalidation.
- [x] Add optimistic concurrency/version checks for metadata and layout edits.
- [ ] Add cloud backup/export procedure and restore drill.
- [ ] Deploy a private pilot app from GitHub.
- [ ] If the Community Cloud private-app slot is occupied, document and execute a
      safe pilot transition; never make real laboratory data public as a
      workaround.

### Checks

- [ ] No Turso token or OIDC secret appears in Git, logs, errors, or downloads.
- [ ] Anonymous and unauthorized users cannot read or write laboratory data.
- [ ] Viewers cannot write; editors cannot administer; admins can perform guarded
      administrative actions.
- [ ] Local and remote repository contract results match.
- [x] Two sessions editing the same run receive a visible conflict rather than
      silent last-write data loss.
- [x] A simulated remote failure produces no partial import.
- [ ] App cold start, run list, run load, metadata save, and plot load timings are
      recorded on Community Cloud.
- [x] Database usage after representative imports is compared with Turso free-tier
      budgets.
- [x] Backup and restore are successfully rehearsed using non-production data.

### Exit gate

An authenticated private pilot reliably creates, reads, edits, exports, and
restores growth runs in Turso Cloud.

### Rollback

Disable cloud writes and return the pilot to read-only. Local mode remains usable,
and production legacy databases remain untouched.

## Phase 6 - Legacy growth import and migration pilot

Goal: migrate existing growth artifacts without risking originals.

### Tasks

- [x] Implement version-detecting importer for legacy growth SQLite files.
- [x] Preserve custom well metadata currently stored in JSON.
- [x] Map legacy plate fields and report fields that were absent from old schemas.
- [x] Detect duplicate run IDs and source hashes.
- [x] Produce per-file preview and verification report.
- [x] Add batch dry-run and batch import commands.
- [x] Test against copies, never original files.

### Checks

- [x] Dry-run writes nothing.
- [x] Imported counts and raw hashes match legacy sources.
- [x] Known legacy metadata loss is reported rather than invented.
- [x] Duplicate imports are skipped or explicitly versioned.
- [x] Randomly sampled curves, well layouts, and background settings match the
      legacy viewer.
- [x] Original files remain byte-for-byte unchanged.

### Exit gate

A representative copy of the legacy growth library imports with auditable reports
and no unexplained differences.

## Phase 7 - MIC module and Turso migration

Goal: add MIC as a module on the shared platform rather than a separate app.

### Tasks

- [x] Add MIC application services using the Phase 2 domain implementation.
- [x] Add MIC plate entry/import wizard and reusable plate-layout editor.
- [x] Add Plate Library, search, MIC result review, visualization, manual-check,
      lock, and soft-delete flows.
- [x] Use indexed server-side filters and pagination.
- [x] Implement legacy MIC SQLite/Turso importer.
- [x] Preserve existing custom labels and checked/locked/deleted states.
- [x] Replace `INSERT OR REPLACE` with explicit upserts that preserve unrelated
      fields.
- [x] Migrate through a dry-run, staging copy, and verification report.
- [ ] Perform the final controlled Turso cutover after real credentials exist.

### Checks

- [x] Golden MIC results and warnings match Phase 1 expectations.
- [x] Editing a plate preserves lock, check, deletion, format, and method fields.
- [x] All multi-table saves are transactional.
- [x] Search and visualization do not load all well rows unnecessarily.
- [x] Custom labels have deterministic grouping/search semantics.
- [x] Legacy and new result counts reconcile on the staging database.
- [ ] Production migration has a tested restore point.

### Exit gate

Growth and MIC operate in one authenticated application with shared experiments,
plates, users, provenance, and deployment infrastructure.

Local/fake-cloud exit evidence is recorded in
`docs/PHASE7_MIC_MODULE_AND_MIGRATION.md`. The authenticated real-Turso deployment
and production restore point remain external gates by design.

## Phase 8 - Standalone packaging and optional offline sync

Goal: restore desktop/offline distribution using the same codebase.

### Tasks

- [x] Package the local Streamlit app for macOS and Windows from the same source
      tree; no copied application folders.
- [x] Store the local database and configuration in an OS-appropriate user data
      directory, not inside the executable bundle.
- [x] Add first-run setup, database selection, backup, and restore.
- [x] Decide that signing/notarization is not yet justified for internal
      pre-release artifacts; require it before broad distribution.
- [x] Measure macOS startup time and package size; collect the same report in the
      Windows package workflow.
- [x] Decide through ADR whether explicit `turso.sync` is needed.
- [x] Do not add push/pull controls because sync was not approved; ADR-0009 lists
      the conflict rules required before reconsideration.

### Checks

- [ ] Execute the GitHub matrix proving macOS and Windows builds use identical
      schema and golden fixtures (workflow is implemented; publication pending).
- [x] Application upgrades preserve the user database.
- [x] Packaged and cloud modes share the same tested portable exporter/importer.
- [x] Offline operation requires no cloud credentials.
- [x] Automatic sync is deferred, so a failed sync cannot corrupt the local
      database.

### Exit gate

Standalone builds pass the same domain, repository, and portable-file compatibility
checks as local development and cloud deployment.

The macOS arm64 build and frozen backup/restore/server smoke are verified in
`docs/PACKAGING_BASELINE.md`. Windows execution remains an external GitHub matrix
gate; the same spec and compatibility suite are already configured.

## Phase 9 - Hardening, release, and legacy retirement

Goal: make the new platform the default without losing recoverability.

### Tasks

- [x] Profile imports, run loading, plotting, search, and exports with realistic
      library sizes.
- [x] Add indexes or caching only in response to measured bottlenecks; the shared
      library baseline required no speculative index/cache changes.
- [x] Add user manual, administrator runbook, backup/restore guide, schema guide,
      and troubleshooting guide.
- [x] Add release versioning, changelog, and database compatibility policy.
- [ ] Run a user-acceptance pilot with real workflows on copied/anonymized data.
- [x] Freeze legacy apps as read-only references; this repository never imports
      or writes them as runtime dependencies.
- [x] Define retention and deletion policy for backups and exports.

### Final checks

- [ ] Full CI, remote contract, migration, UI smoke, and packaging suites pass.
- [x] Local/fake-cloud backup restore is demonstrated from scratch; real Turso
      restore remains an external gate.
- [x] No unresolved critical local/fake-cloud data-integrity, security, or
      migration issues remain; hosted risks stay explicitly unsupported.
- [x] Local/fake-cloud performance budgets pass with the expected number of runs.
- [x] Human and AI developers can set up, test, diagnose, and extend the project
      using repository documentation only.

### Exit gate

The new repository becomes the supported system; legacy apps remain archived until
the agreed retention period ends.

Local implementation and hardening evidence is complete. GitHub CI/Windows
execution, real Turso/OIDC/restore, and the human UAT/cutover remain deliberate
external acceptance gates, not simulated checks.

## Recommended reasoning and collaboration settings

- Architecture, schema, migration, and conflict-resolution planning: `xhigh`.
- Normal implementation and testing after contracts freeze: `high`.
- Routine documentation/mechanical cleanup: `medium` or `high`.
- `ultra` is not currently justified. Reconsider it only for a difficult sync
  conflict model, unexplained migration discrepancies, or a high-stakes security
  review.

Use parallel agents only for bounded modules with frozen interfaces. With four
total concurrency slots, the preferred pattern is one integration owner plus at
most three workers. Integration, schema changes, and migration cutover remain
serial responsibilities.

## Immediate next action

Create the private GitHub repository, add it as `origin`, push `main`, and let the
configured CI and macOS/Windows packaging matrix run. Keep fake-cloud as the
default until the user deliberately supplies real Turso and OIDC credentials;
then execute the still-open remote contract, authorization, backup/restore,
deployment, UAT, and cutover gates in order.
