# User guide

## Choose a mode

- `make run-local` starts the no-credential fake-cloud development app.
- `make run-standalone` starts the source-based offline app with local `pyturso`.
- A packaged `PlateReaderDatabase` executable creates its database outside the
  bundle; see the standalone guide.
- Hosted Turso mode remains disabled until its credentials and authentication are
  configured and verified.

## Growth runs

Open **New Growth Run** and follow the five saved steps: choose a CSV, validate
time/wells, enter metadata, review the 96-well layout, and commit. A valid source
has well columns such as `A1` through `H12`; `Time` is optional when a sampling
interval is supplied. The preview reports missing wells and parser warnings.

Use **Growth Run Library** for indexed search and open one workspace. Metadata
and layout changes require an explicit save. Raw observations never change.
Background subtraction creates a versioned analysis revision. Select a bounded
set of wells before rendering curves. Export produces a checksummed standard
SQLite artifact containing the selected plate and current revisions.

**Overview & QC** shows a selectable channel/timepoint heatmap. Choose raw or
background-corrected values; the caption states the exact channel, time index,
elapsed time, and correction state. After a background revision exists, the tab
also shows group/channel CV summaries and detailed stored timepoint statistics.
Open **96-well curve overview** and render it when the full 8x12 small-multiple
view is needed; it is lazy and cached so routine reruns do not rebuild all 96
curves.

Editors and administrators can open **Time-course background correction** to
copy Media, Strain, Group, or Treatment across all background-group assignments,
then calculate a new immutable revision. If blank assignments or groups change,
the prior revision is marked stale and is not used for corrected plots until it
is recomputed.

Use the reusable selector in **Plotting** to stage wells with the 8x12 plate,
selection list, or metadata add/remove filters. Saving the selection changes the
default for later sessions; rendering does not require saving it. Curve colors
can follow plate order, plotted-series order, or any available metadata/custom
field. Choose **Curve label** to name traces from Display name or another saved
well field. PNG, vector PDF, and both CSVs correspond to the visible plot. The
database-oriented long CSV includes exact time identities, raw and plotted
values, correction state, revision identity, and well metadata. The plot-oriented
wide CSV puts time first and one visible curve in each remaining column.

**Background history** explains each saved background calculation. “Current ·
ready” means its input still matches the saved blank and group assignments;
“Current · stale” means those assignments changed and corrected plots use raw
fallback values until recomputation from **Overview & QC**. Historical records
are read-only, and their identifiers, hashes, parameters, and versions remain in
the technical-details expander.

**Activity log** is the append-only audit trail for the run. Its main table puts
the action, before/after edit summary, user, and timestamp first for saved events
such as imports, metadata/layout edits, and background recalculations. Viewing a
run and rendering plots are intentionally not logged. Open the technical-details expander to see the
original event IDs, entity IDs, event types, and stored payloads. Browser file
downloads do not currently create a database audit event.

Use **Dark mode** in the sidebar for a darker interface and plot. The preference
lasts for the current browser session. For a task-oriented walkthrough and
diagram, see the [Growth workflow user guide](GROWTH_USER_GUIDE.md).

Both import and saved-run layout editors include **Reusable plate templates**.
Any signed-in user can apply a Growth template to the staged 96-well layout;
administrators can save, overwrite, and delete templates. Applying one never
replaces labels imported from the source file, and it reaches the database only
after the normal commit or full-layout save.

Administrators can also save frequently reused fill values for strain, media,
treatment, units, and Growth grouping fields. These appear as suggestions in
the fill helper; the field remains editable, so a new value never has to be
registered before use.

## MIC modules

MIC modules remain in the repository for possible future work, but **MIC Plate
Library**, **New MIC Plate**, **MIC Workspace**, and **MIC Results** are hidden
from the normal application. They are not part of the current Growth workflow,
and users do not need to take any MIC-specific action.

## Portable transfer

Download a portable export from either workspace. In the receiving app, open
**Import Portable Data**, upload it (or select a local path in standalone mode),
and preview first. Review table counts and collisions. **Remap incoming IDs
safely** is the normal choice when the destination already contains related
records; strict rejection is useful for controlled migrations. The commit is
transactional, never overwrites existing records, and repeated import of the
same export adds nothing.

Portable import is not a full installation restore. Use complete backup/restore
for disaster recovery.

## Roles and lifecycle

- Viewer: search, inspect, plot, preview portable data, and export.
- Editor: viewer actions plus imports, metadata/layout edits, revisions, and
  manual review state.
- Admin: editor actions plus template management, lock, soft delete/restore, and
  user administration.

Deletion is soft. Locked MIC plates cannot be deleted. If an operation fails,
copy the diagnostic ID shown in the UI before contacting an administrator.
