# Growth Workspace Improvement Plan

Status: approved implementation handoff plan, 2026-08-01.

Source feedback: `user_feedback_20270801.md`.

Recommended executor: **gpt-5.6-sol, medium reasoning**. Use one phase per
implementation turn. Do not use Sol high unless medium repeatedly fails to resolve
Streamlit state synchronization, background-correction parity, or a full-suite
regression. Terra high is acceptable for a narrow UI-only follow-up, but Sol medium
is preferred for the cross-module work in this plan.

## Objective

Improve the existing Growth workspace without replacing its UI direction or
rewriting its backend. Add efficient 96-well selection, display-name creation,
timepoint-aware heatmaps, deterministic plot colors, selected-data export, and
plain-language analysis history. Preserve all current rich metadata, the shared
8x12 plus 96-row editor, raw-data immutability, time-matched background correction,
and local/fake-cloud compatibility.

This is an incremental improvement plan. It is not authorization to restart the
application or copy an old UI wholesale.

## Current verified baseline

- Baseline commit when this plan was written: `5d9b73b`.
- Full suite: 333 passed, 2 skipped, 90.37% coverage.
- Formatting, Ruff, mypy, secret scan, and launcher syntax pass.
- The macOS ARM PyArrow editor crash is covered by an out-of-process regression
  test and must remain fixed.
- Growth raw observations are immutable after commit.
- Growth background correction is already calculated by background group,
  channel, and timepoint.
- A changed blank/background-group assignment makes the current background
  revision stale, and stale rows are withheld from plots.
- The current Growth Layout screen already uses one synchronized 8x12 plate and
  96-row table. Keep it.

## Non-goals and hard boundaries

Do not work on any of the following in this plan:

- portable data import;
- portable database format, collision behavior, or migration;
- MIC import, library, results, calculations, visualization, or UI;
- Turso credentials, real remote Turso, OIDC, deployment, or hosting;
- database schema changes unless an existing field is proven insufficient and an
  ADR is approved first;
- a new application shell or another switch between new and legacy UI styles;
- edits to either legacy application; they are read-only behavioral references;
- automatic database writes while the user is editing a staged grid or form.

The existing whole-run portable Export tab may remain visible and operational,
but it is not to be modified in these phases.

## Engineering rules for every phase

1. Read this file, the user feedback, ADR-0012 through ADR-0016, and the relevant
   current code before editing.
2. Implement only the next incomplete phase. Do not combine all phases into one
   large turn or commit.
3. Keep application services independent of Streamlit and Plotly.
4. UI code may call typed application services; it must not duplicate scientific
   formulas or issue ad hoc SQL.
5. Keep exactly one canonical staged selection and one canonical staged layout.
   Grid, table, filters, and previews are views of that state.
6. Preserve optimistic concurrency and the existing explicit final save actions.
7. Never rewrite raw observations for metadata, layout, selection, naming,
   plotting, or export operations.
8. Add targeted tests before or with each behavior change.
9. Run the phase gate before committing. Leave `main` runnable after every phase.
10. Do not stage `user_feedback_20270801.md` unless the user explicitly requests
    it. Do not overwrite or reformat that file.

## Intended module boundaries

Prefer small Growth-specific modules over adding more unrelated behavior to the
already large `src/plate_reader/ui/pages.py`.

Potential new modules, subject to normal code review:

- `src/plate_reader/application/services/growth_selection.py`
  - pure well-filter and selection operations;
- `src/plate_reader/application/services/growth_display_names.py`
  - deterministic name construction and CSV validation;
- `src/plate_reader/application/services/growth_heatmap.py`
  - channel/timepoint heatmap preparation that reuses current correction logic;
- `src/plate_reader/application/services/growth_data_export.py`
  - selected plotted-data CSV construction;
- `src/plate_reader/ui/growth_selector.py`
  - synchronized 8x12, list, and metadata-filter controls;
- `src/plate_reader/ui/growth_display_names.py`
  - builder, preview, and layout-template upload controls;
- `src/plate_reader/ui/growth_overview.py`
  - timepoint/channel heatmap and background explanation;
- `src/plate_reader/ui/growth_plotting.py`
  - Growth plotting controls and download row.

`pages.py` should remain the workspace orchestrator. Move existing functions only
when the move is mechanical, covered by tests, and performed in the phase that
owns the behavior. Do not perform a broad UI refactor before feature work.

## Phase 0 — Characterize and freeze the current Growth UI

Goal: prevent another accidental feature loss or UI-direction change.

### Tasks

- [x] Record current Growth workspace tabs, labels, save boundaries, and session
      keys in focused characterization tests.
- [x] Confirm the shared Layout editor still exposes both `96-well plate` and
      `Full well table` views.
- [x] Confirm raw count/hash invariants for metadata, layout, and saved plot
      selection updates.
- [x] Add a focused Growth-feedback UI test file rather than expanding one giant
      smoke test indefinitely.
- [x] Evaluate baseline screenshots. No new screenshot is needed because focused
      interaction tests directly freeze the tab/editor/save behavior.

### Likely files

- `tests/ui/test_growth_workspace_feedback.py` (new)
- `tests/integration/test_growth_workflow_services.py`
- no production behavior changes

### Gate

- [x] Existing full test suite remains green (334 passed, 2 skipped).
- [x] Tests prove that both Layout views and explicit save boundaries exist.
- [x] Tests prove that metadata/layout changes do not alter raw observations.

### Suggested commit

`Characterize current Growth workspace behavior`

## Phase 1 — Reusable staged 96-well selection

Goal: create one selection model shared later by plotting, display-name generation,
and selected-data export.

### Functional contract

The canonical selection is an ordered tuple/set of physical positions A1-H12.
The user may change it through:

- an 8x12 boolean checkbox grid;
- a synchronized 96-row list containing well, display name, and selected state;
- select all, clear all, and invert actions;
- row and column shortcuts;
- metadata filters over first-class and custom well fields.

Supported operations are explicit:

- **Replace**: selection becomes the matching wells;
- **Add**: matching wells are added;
- **Remove**: matching wells are removed;
- **Keep only**: current selection is intersected with matching wells.

Filter semantics:

- multiple selected values within one field are OR;
- filters across different fields are AND;
- an empty filter does nothing and must not silently clear the selection;
- matching is normalized but stored metadata is not rewritten.

### Tasks

- [x] Add typed/pure selection operations with physical plate ordering.
- [x] Support Strain, Treatment, Concentration, Media, Group, Replicate, Display
      name, Raw label, and custom columns present in the loaded layout.
- [x] Build a Growth-only reusable selector component.
- [x] Keep the grid, list, filter controls, and session state synchronized.
- [x] Allow staged selection to render a plot immediately.
- [x] Preserve the existing `plot_selected` persistence behavior behind an
      explicit `Save well selection` action.
- [x] Never save selection merely because Streamlit reran.

### Likely files

- `src/plate_reader/application/services/growth_selection.py` (new)
- `src/plate_reader/application/services/__init__.py`
- `src/plate_reader/ui/growth_selector.py` (new)
- `src/plate_reader/ui/pages.py` or `src/plate_reader/ui/growth_plotting.py`
- `tests/unit/test_growth_selection.py` (new)
- `tests/ui/test_growth_workspace_feedback.py`

### Checks

- [x] Every result contains unique valid A1-H12 positions in physical order.
- [x] Replace/add/remove/keep-only are independently unit tested.
- [x] OR-within-field and AND-across-fields are tested.
- [x] Custom-column filtering is tested.
- [x] Grid and list edits produce identical canonical selections.
- [x] Selection persists across ordinary Streamlit reruns.
- [x] Saving selection changes only well layout metadata, not observations.

### Gate

The selector is reliable by itself before any naming or export feature depends on
it. Do not begin Phase 2 or Phase 4 if selection synchronization is flaky.

### Suggested commit

`Add reusable Growth well selection`

## Phase 2 — Display-name builder and layout CSV

Goal: let users construct all 96 display names efficiently and safely without
typing one name at a time.

### Formula-builder contract

The user chooses ordered tokens from available plate and well metadata. Initial
well-token candidates should include:

- Well and Raw label;
- Strain;
- Treatment;
- Concentration and Concentration unit;
- Media;
- Group;
- Inoculum size and Inoculum unit;
- Replicate;
- custom layout columns.

Plate-level candidates may include experiment name, plate name, date, project,
instrument, temperature, channel, and tags. Plate-level values repeat by design
when selected.

Options:

- ordered fields;
- separator;
- optional prefix and suffix;
- omit empty tokens;
- deterministic numeric formatting;
- apply to all wells or the Phase 1 staged selection;
- preview before applying;
- overwrite confirmation when a target already has a display name.

Applying the preview updates only the staged Layout DataFrame. The existing
`Save full layout` action remains the sole database boundary.

### CSV contract

- Download an Excel-friendly UTF-8 CSV with headers `Well,Display name`.
- Accept complete or partial uploads.
- Treat well positions case-insensitively and normalize them to A1-H12.
- Reject unknown wells, duplicate wells, duplicate headers, or a missing required
  header.
- A partial file changes only listed wells.
- Empty display names require explicit confirmation before clearing values.
- Show an additions/changes/clears preview before applying to staged state.
- This is a layout helper, not portable database import.

### Tasks

- [x] Add pure deterministic display-name composition.
- [x] Add CSV generation, parsing, validation, and change preview.
- [x] Add a `Display name builder` expander or dialog inside the existing Growth
      Layout tab.
- [x] Reuse the shared staged Layout frame and Phase 1 selection.
- [x] Synchronize results into both the existing 8x12 Display name view and the
      full table.

### Likely files

- `src/plate_reader/application/services/growth_display_names.py` (new)
- `src/plate_reader/application/services/__init__.py`
- `src/plate_reader/ui/growth_display_names.py` (new)
- `src/plate_reader/ui/pages.py`
- `tests/unit/test_growth_display_names.py` (new)
- `tests/ui/test_growth_workspace_feedback.py`

### Checks

- [x] Field order changes output predictably.
- [x] Empty values do not produce repeated separators.
- [x] Numeric formatting is locale-independent and stable.
- [x] Selected-only application leaves other wells byte-for-byte unchanged.
- [x] Partial CSV does not erase unlisted wells.
- [x] Invalid/duplicate wells fail before staged state changes.
- [x] Saving names leaves raw observation count/hash unchanged.
- [x] Both Layout views show the same names after apply and after reload.

### Suggested commit

`Add Growth display name builder`

## Phase 3 — Channel- and timepoint-aware Overview heatmap

Goal: inspect the plate at any measured Growth timepoint without confusing an
endpoint visualization with time-course background correction.

### Scientific contract

- Populate channel choices from actual stored observations.
- Populate time choices from actual time indices/elapsed times for the selected
  channel; do not assume a fixed ten-minute interval.
- Default to the final available timepoint for backward familiarity.
- Select exact stored timepoints rather than silently choosing an approximate
  nearest time.
- Offer Raw and Background-corrected states.
- Reuse `PrepareGrowthPlotDataService` or the same domain correction path; do not
  implement a second subtraction formula for heatmaps.
- If correction is unavailable or stale, follow the existing raw-fallback and
  warning behavior.
- Never combine values from different channels in one heatmap.

### UI contract

Place Channel, Timepoint, and Value controls directly above the heatmap. The
caption must state the selected channel, elapsed minutes, and raw/corrected state.
Hover details should include well, display name, raw value, plotted value, and
background mean when available.

Rename `Background assignment and recompute` to `Time-course background
correction` and explain:

> Blank wells are summarized within each background group at every timepoint. The
> matching timepoint background is subtracted from sample wells.

Keep background assignment/recompute available, preferably collapsed unless no
revision exists or the current revision is stale. Do not remove the backend.

### Tasks

- [x] Add a typed heatmap DTO/preparation service.
- [x] Make channel and timepoint identity explicit in cache keys.
- [x] Update the Growth Overview controls and hover labels.
- [x] Preserve current background QC summary and detailed timepoint table.
- [x] Preserve the lazy 96-well curve overview.

### Likely files

- `src/plate_reader/application/services/growth_heatmap.py` (new)
- `src/plate_reader/application/services/__init__.py`
- `src/plate_reader/ui/growth_overview.py` (new) or a focused extraction from
  `src/plate_reader/ui/pages.py`
- `src/plate_reader/ui/plotting.py`
- `tests/unit/test_growth_heatmap.py` (new)
- `tests/ui/test_growth_workspace_feedback.py`

### Checks

- [x] Earliest, middle, and final timepoints return expected cells.
- [x] Multi-channel data cannot overwrite or mix cells.
- [x] Raw cells match immutable observations exactly.
- [x] Corrected cells match prepared Growth plot values at the same channel and
      timepoint.
- [x] Missing/stale background cases warn and fall back consistently.
- [x] Changing only the UI timepoint does not query/write the database again.

### Suggested commit

`Add timepoint-aware Growth heatmap`

## Phase 4 — Plot selection and deterministic colors

Goal: make detailed curve selection efficient and visual interpretation stable.

### Tasks

- [x] Replace the Plotting tab's well multiselect with the Phase 1 selector.
- [x] Keep staged selection distinct from explicitly saved default selection.
- [x] Show selected-well count and a compact selected-well summary.
- [x] Retain raw/corrected toggle, axes, symmetric-log option, title, PNG, and PDF.
- [x] Add color modes:
  - rainbow by physical plate order;
  - rainbow by plotted-series order;
  - categorical by Strain;
  - categorical by Treatment;
  - categorical by Media;
  - categorical by Group;
  - categorical by another available categorical/custom column.
- [x] Make categorical mode assign the same color to the same category.
- [x] Keep repeated display names distinct by including physical well in the
      series identity.
- [x] Produce one deterministic series/color mapping and reuse it in Plotly and
      the vector PDF so screen and download agree.
- [x] Continue using WebGL and avoid eager rendering of all 96 curves.

### Likely files

- `src/plate_reader/ui/growth_plotting.py` (new or focused extraction)
- `src/plate_reader/ui/plotting.py`
- `src/plate_reader/application/services/growth_plotting.py`
- `src/plate_reader/application/services/growth_plot_export.py`
- `tests/unit/test_growth_plotting.py`
- `tests/unit/test_growth_plot_figure.py`
- `tests/unit/test_growth_plot_pdf.py`
- `tests/ui/test_growth_workspace_feedback.py`

### Checks

- [x] The plotted position set exactly equals the staged selector set.
- [x] Add/remove filter combinations work without reopening Layout.
- [x] Colors and legend order are stable across reruns.
- [x] Plotly and PDF use the same series colors.
- [x] Repeated display names do not merge curves.
- [x] Empty selection produces guidance instead of an exception.
- [x] A 96-well selection remains within the recorded performance baseline
      (13,920-point style plus Plotly build: 0.139 s on the baseline machine).

### Suggested commit

`Improve Growth plot selection and colors`

## Phase 5 — Export exactly the selected plotted data

Goal: download analysis-ready data that matches the visible plot, directly beside
the plot downloads.

### CSV contract

Use long format with one row per prepared plot point. Include:

- plate ID, experiment name, and plate name;
- current background revision ID or `raw`;
- Well and Display name;
- Group, Media, Strain, Inoculum size/unit, Replicate, Treatment,
  Concentration/unit, Notes, and available custom columns;
- Channel, Time index, elapsed microseconds, and elapsed minutes;
- Raw value, background mean, plotted value, and correction-applied flag.

Generate the CSV from the same selected positions, correction choice, and prepared
plot data used to render the current graph. Do not independently reload or
recalculate a different selection during download.

Place `Download selected plot data as CSV` in the same download row as PNG/PDF.
Use an Excel-friendly UTF-8 encoding and a safe filename derived from the plot
title or plate identifier.

### Tasks

- [x] Ensure the prepared plot DTO retains exact time index/microseconds required
      for lossless export; extend it additively if needed.
- [x] Add a pure CSV artifact builder.
- [x] Add the download button beside the existing plot PDF action.
- [x] Do not alter the whole-run portable Export tab.

### Likely files

- `src/plate_reader/application/services/growth_data_export.py` (new)
- `src/plate_reader/application/services/growth_plotting.py`
- `src/plate_reader/application/services/__init__.py`
- `src/plate_reader/ui/growth_plotting.py` or `src/plate_reader/ui/pages.py`
- `tests/unit/test_growth_data_export.py` (new)
- `tests/ui/test_growth_workspace_feedback.py`

### Checks

- [x] Exported wells exactly match the current plot selection.
- [x] Row count equals the number of prepared plot points, including channels.
- [x] Raw values equal stored observations without rounding changes.
- [x] Plotted values equal the visible plot data.
- [x] Correction state and revision identity are explicit.
- [x] First-class and custom well metadata are present.
- [x] CSV opens cleanly in Excel and handles commas/newlines through standard CSV
      quoting.

### Suggested commit

`Export selected Growth plot data`

## Phase 6 — Explain analysis history and activity log

Goal: make existing audit concepts understandable without changing persistence.

### UI terminology

- Rename `Revisions` to `Background history`.
- Explain that a background revision is a saved, versioned calculation over
  immutable raw measurements plus the blank/background assignments that existed
  at that time.
- Explain current versus stale status and why recomputation may be required.
- Avoid duplicate recompute actions: keep the primary action in the Overview
  background section; history should primarily explain and display records.
- Rename `Provenance` to `Activity log`.
- Explain that it records who imported, edited, recalculated, or exported data and
  when.
- Present friendly action, user, and timestamp columns first.
- Preserve technical identifiers and payloads in an optional details expander.

### Tasks

- [x] Add focused presentation functions for background history and activity log.
- [x] Change labels/captions without changing revision or provenance schemas.
- [x] Document the two concepts in `docs/USER_GUIDE.md`.
- [x] Update UAT instructions for the completed Growth workflow.

### Likely files

- `src/plate_reader/ui/pages.py` or a new focused Growth history UI module
- `docs/USER_GUIDE.md`
- `docs/UAT_CHECKLIST.md`
- `tests/ui/test_growth_workspace_feedback.py`

### Checks

- [x] Current and stale background states are understandable without exposing only
      hashes/IDs.
- [x] Historical revisions remain read-only and complete.
- [x] Activity records remain complete and auditable.
- [x] Friendly formatting does not discard technical detail.
- [x] No repository or schema change is introduced.

### Suggested commit

`Clarify Growth background history and activity log`

## Phase 7 — Final Growth regression and user-acceptance gate

Goal: prove the complete feedback workflow without touching deferred areas.

### Automated gate

Run from the repository root:

```bash
.venv/bin/ruff format --check src tests app.py
.venv/bin/ruff check src tests app.py
.venv/bin/mypy src app.py
.venv/bin/pytest -q
.venv/bin/python scripts/scan_secrets.py
git diff --check
bash -n "Start Plate Reader.command"
```

Required assertions:

- [ ] full suite and coverage threshold pass;
- [ ] real dual-view Arrow regression passes;
- [ ] raw observation count and hash remain unchanged through metadata, layout,
      naming, selection, plotting, and CSV export;
- [ ] local and fake-cloud repository behavior remains equivalent;
- [ ] no MIC or portable-import snapshot/test changed merely to accommodate this
      work;
- [ ] no new eager 96-curve render is introduced;
- [ ] no secrets or real laboratory data are tracked.

### Manual Growth UAT

- [ ] Start by double-clicking `Start Plate Reader.command`.
- [ ] Import a representative anonymized Growth CSV.
- [ ] Complete rich metadata and the existing dual-view Layout workflow.
- [ ] Build display names from several ordered fields and inspect the preview.
- [ ] Download, edit in Excel, and re-upload a partial display-name CSV.
- [ ] Save and reload the layout; confirm names in both views.
- [ ] Select wells physically and with metadata add/remove operations.
- [ ] Inspect earliest, middle, and final heatmap timepoints for one channel.
- [ ] Compare raw and background-corrected values.
- [ ] Render a selected plot with rainbow and categorical colors.
- [ ] Download PNG, PDF, and selected-data CSV.
- [ ] Confirm the CSV contains exactly the visible selected series and correction
      state.
- [ ] Read Background history and Activity log without needing undocumented field
      definitions.
- [ ] Restart the app and confirm persisted metadata/layout/default selection.
- [ ] Confirm portable import and MIC areas were not modified as part of this UAT.

### Exit gate

The user can complete the entire Growth workflow above on anonymized data, all
automated checks pass, and no critical Growth defect remains. Deferred portable
import and MIC work stays deferred.

## Parallelism and ownership

Phase 0 and Phase 1 are sequential because all later work depends on one stable
selection contract.

After Phase 1 is green, limited parallel work is safe only with separate file
ownership:

| Owner | Scope | Exclusive production files |
| --- | --- | --- |
| Display-name worker | Phase 2 pure service and unit tests | `growth_display_names.py` |
| Heatmap worker | Phase 3 pure service and unit tests | `growth_heatmap.py` |
| Integration owner | Shared exports and Streamlit wiring | `services/__init__.py`, `pages.py`, UI modules |

Do not let multiple workers edit `pages.py`, `application/services/__init__.py`,
or the same UI test simultaneously. Plotting Phase 4 and export Phase 5 are
sequential because they must share the same prepared plot DTO and selection.

A single Sol-medium executor is the safest low-coordination option. Parallelism
is optional, not required.

## Handoff prompt for the coding agent

Use the following instruction when handing off a phase:

> Read `docs/GROWTH_WORKSPACE_IMPROVEMENT_PLAN.md`,
> `user_feedback_20270801.md`, and ADR-0012 through ADR-0016 completely. Implement
> only the next incomplete phase. Preserve the current Streamlit shell and shared
> 8x12/96-row Layout editor. Do not work on portable import or any MIC code. Keep
> raw observations immutable and reuse the existing timepoint-specific background
> correction. Add targeted tests, run the phase gate, update only that phase's
> checkboxes in the plan, and create one focused commit. If a requirement is
> unclear or conflicts with current behavior, stop and ask a specific question
> instead of changing UI direction.

## When to stop and ask the user

Stop rather than guessing if implementation would require:

- replacing the shared Layout editor;
- changing database schema or raw observation storage;
- choosing a destructive interpretation of a partial display-name CSV;
- changing the scientific background formula;
- modifying portable import or MIC behavior;
- removing an existing Growth capability;
- introducing a new application framework or hosted service.
