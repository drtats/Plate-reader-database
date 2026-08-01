# Plate Reader Database

A fresh, modular plate-reader data platform for growth-curve and MIC workflows.

The planned application uses one Python/Streamlit codebase with interchangeable
local and cloud persistence:

- Local and standalone operation: `pyturso` with a local database file.
- Streamlit Community Cloud: the official Python `libsql` driver connected
  directly to Turso Cloud.
- Future offline synchronization: explicit `turso.sync` push/pull.

The existing growth-curve and MIC repositories are legacy reference
implementations. They remain outside this repository and are not runtime
dependencies.

## Current status

The shared growth and MIC workflows, local `pyturso`, isolated fake-cloud, and
official remote `libsql` adapters, legacy migration tools, portable exchange,
OIDC login gate, database-backed roles, and macOS standalone package are
implemented and tested locally. Imports are atomic and idempotent;
metadata/layout edits preserve immutable raw observations; analyses are
revisioned; queries and plots are bounded; and complete backup/restore is
verified. Live Turso contract execution, GitHub publication, hosted OIDC
configuration, and cutover remain pending until accounts and credentials exist.

Start here:

1. [Architecture](docs/ARCHITECTURE.md)
2. [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
3. [Contributor and AI-agent rules](AGENTS.md)
4. [Growth v4 comparison and UI record](docs/PHASE4_GROWTH_COMPARISON.md)
5. [Deployment runbook](docs/operations/DEPLOYMENT_RUNBOOK.md)
6. [Backup and restore](docs/operations/BACKUP_RESTORE.md)
7. [Legacy growth migration](docs/PHASE6_LEGACY_GROWTH_MIGRATION.md)
8. [MIC module and migration](docs/PHASE7_MIC_MODULE_AND_MIGRATION.md)
9. [Standalone guide](docs/operations/STANDALONE.md)
10. [User guide](docs/USER_GUIDE.md)
11. [Administrator runbook](docs/operations/ADMIN_RUNBOOK.md)
12. [Schema guide](docs/SCHEMA_GUIDE.md)
13. [Troubleshooting](docs/operations/TROUBLESHOOTING.md)

## Local development

Prerequisites:

- Git;
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).

`uv` reads `.python-version`, installs Python 3.12 when necessary, and creates the
ignored project environment:

```bash
uv sync --all-groups
make check
make run-local
```

Create or safely re-use a deterministic 13,920-measurement demo run:

```bash
uv run python scripts/seed_demo.py demo.sqlite --backend fake-cloud
```

Measure both local persistence adapters with the same full-run workload:

```bash
make benchmark
```

See [the recorded Phase 3 baseline](docs/PERFORMANCE_BASELINE.md) for results and
regression budgets.

The smoke application defaults to `fake-cloud`. This mode uses no Turso
credentials and must never be treated as proof that real cloud behavior works.
Copy `.env.example` only for local configuration; do not commit the resulting
`.env` file.

## Deployment status

GitHub remote creation and Streamlit Community Cloud smoke deployment are the
remaining external Phase 0 checks. Real Turso setup is intentionally deferred.
The remote adapter and operational commands are ready; see the deployment
runbook before supplying credentials.

## Guiding principles

- Preserve raw experimental data; derive analyses without mutating raw values.
- Keep Streamlit, database drivers, and filesystem operations outside the domain
  layer.
- Use the same schema and repository contract locally and in Turso Cloud.
- Make imports idempotent and exports portable.
- Add tests before porting behavior from a legacy application.
- Prefer explicit migrations, transactions, and save actions over hidden
  autosaves and runtime schema changes.
