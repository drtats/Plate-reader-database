# Standalone application guide

The standalone distribution is the same Python package, Streamlit entry point,
schema, migrations, and domain code used by local development and cloud
deployment. It does not contain a copied application fork and requires no Turso
credentials.

## First run

Launching `PlateReaderDatabase` creates a versioned desktop configuration and a
local database in the operating system's user-data directory:

| Platform | Default directory |
| --- | --- |
| macOS | `~/Library/Application Support/Plate Reader Database` |
| Windows | `%LOCALAPPDATA%\Plate Reader Database` |
| Linux development | `$XDG_DATA_HOME/plate-reader-database` or `~/.local/share/plate-reader-database` |

The database and `backups/` directory live outside the executable bundle, so
replacing or upgrading the application does not replace user data.

## Launcher commands

```text
PlateReaderDatabase run [--port 8501] [--no-browser]
PlateReaderDatabase init
PlateReaderDatabase info
PlateReaderDatabase backup [DESTINATION]
PlateReaderDatabase restore BACKUP [--destination NEW_DATABASE]
```

Global `--data-dir DIRECTORY` selects a nondefault data directory. Global
`--database DATABASE` selects and remembers a database. Restore always creates a
new file and then selects it; it refuses to overwrite the active database.

Backup and restore execute schema validation, row counts, logical per-table
hashes, and integrity checks. Keep the previous database until the restored copy
has been opened and sampled successfully.

## Building

Install the locked environment and build on the target operating system:

```bash
uv sync --all-groups --frozen
uv run python scripts/build_standalone.py
uv run python scripts/smoke_standalone.py
```

PyInstaller produces `dist/PlateReaderDatabase.app` on macOS and
`dist/PlateReaderDatabase/PlateReaderDatabase.exe` on Windows. GitHub's manually triggered or
tag-triggered `package.yml` workflow builds macOS arm64 and Windows x64 from the
same specification, runs shared domain/repository/portable compatibility tests,
smokes each frozen launcher, and uploads the distributions plus measurement
reports.

## Signing and distribution

Version 0.1 packages are internal pre-release artifacts. Apple notarization and
Windows code signing are not configured because no public distribution identity
or requirement exists yet. Add signing in the release workflow before broad
distribution; never store signing credentials in the repository.

## Offline exchange

Automatic synchronization is intentionally absent. Transfer selected runs using
the checksummed portable SQLite export/import flow. See ADR-0009 for the conflict
rules that must be designed before automatic sync is considered.
