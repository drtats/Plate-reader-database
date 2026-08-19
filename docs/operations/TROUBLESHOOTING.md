# Troubleshooting

## The app does not start

Run `PlateReaderDatabase info` for standalone paths or inspect the environment
variables in `.env.example`. Fake-cloud is invalid in production. A port conflict
can be avoided with `run --port 8502`. Do not move the database into the
executable bundle.

## Import validation fails

- Confirm UTF-8 CSV, canonical/alias headers, finite numeric values, and unique
  wells/times.
- Growth imports are limited to 25 MiB; MIC imports to 5 MiB; portable imports to
  25 MiB. Split transfer archives by plate rather than increasing memory limits
  casually.
- MIC commit requires all 96 wells. Growth partial plates are allowed but report
  missing wells.
- A source hash error means the bytes changed between preview and commit; preview
  the source again.

## The app closes after saving metadata

If Terminal reports `Segmentation fault: 11` while Step 4 opens, update to a
version containing ADR-0020 and restart with `Start Plate Reader.command`. The
launcher and application now force Arrow's system memory pool before rendering
the 8x12 and 96-row editors. This is a native Arrow allocator failure, not a
database error. During a new import, Step 3 only stages metadata in the browser
session, so restart the import; no plate is committed until Step 5. In the
saved-run workspace, an explicit metadata save may already have committed before
a later crash.

If pressing **Render selected curves** reconnects the app and restores A1-A8,
start through the supported launcher so ADR-0020's Arrow allocator setting is
active. The plot selector must not write `plot_selected`, fall back to A1-A8, or
let the Selection List overwrite the submitted 8x12 grid.

## A save reports a concurrency conflict

Another session saved the plate after it was loaded. Reload, compare the latest
metadata/layout, and reapply the intentional change. Never retry with a fabricated
timestamp.

## Portable import reports collisions

Normal transfers into a populated database should use safe remapping. Strict
rejection is for migrations that require globally unchanged IDs. Existing rows
are never overwritten. If the exact export was already imported, the idempotency
report says no new rows were created.

## A plot is slow or absent

Select a bounded number of growth wells and render explicitly. Confirm the plate
has raw rows and, for corrected plots, a current background revision. Clear the
browser session/cache after switching databases. Record operation timing and
query plan before adding a new cache or index.

## Backup/restore fails

Destinations must not already exist and restore cannot target the active file.
Check free disk space and permissions. A schema/checksum/integrity error means the
artifact is not acceptable; preserve it for investigation and restore a known
good backup instead.

## Reporting a defect

Include application version, environment/storage mode, operating system, exact
reproduction steps, diagnostic ID, and whether synthetic data reproduces it.
Never attach credentials or an identifiable laboratory database to a public
issue.
