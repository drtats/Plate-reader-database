# Growth workspace Phase 7 verification

Date: 2026-08-01  
Baseline: `5d9b73b`  
Final implementation range: `d1df821` through `e57b226`, plus this report/final
presentation correction.

## Automated gate

- Ruff formatting and lint passed.
- Mypy passed for `src` and `app.py`.
- Pytest: 382 passed, 2 skipped, 90.41% coverage.
- The out-of-process macOS ARM Arrow regression passed.
- Secret scan, diff check, and launcher Bash syntax passed.
- Growth raw count/hash invariants passed through metadata, layout, display-name,
  selection, plot, and selected-data CSV workflows.
- Repository contract/scale tests passed for local and fake-cloud adapters.
- Diff review from `5d9b73b` contains no MIC or portable-import file changes.
- The complete 96-well curve overview remains behind its explicit Render button.

## Live local browser UAT

The existing process on port 8501 was confirmed to be the exact
`Start Plate Reader.command` Streamlit command for this repository. It was left
running and tested without modifying its saved plate.

Verified in the live app:

- fake-cloud mode and the existing Growth workspace opened normally;
- Overview exposed channel, timepoint, and raw/corrected heatmap controls;
- Layout retained synchronized `96-well plate` and `Full well table` views;
- Plotting exposed the 8x12/list/filter selector, count/summary, axes, symmetric
  log, and deterministic color control;
- eight selected WebGL curves rendered;
- PDF and selected-data CSV download events completed;
- the Plotly PNG toolbar action was present and invoked, although the in-app
  browser did not expose its client-side download as an observable download event;
- Background history showed a friendly current/ready row and retained technical
  revision details;
- Activity log showed friendly action/user/time rows and retained complete event
  IDs, entity IDs, hashes, and payloads;
- no application console errors were observed.

## Remaining human acceptance

The unchecked manual checklist in `GROWTH_WORKSPACE_IMPROVEMENT_PLAN.md` remains
for the user to perform with a representative anonymized laboratory CSV and
Excel. Automated tests use synthetic data and cannot replace the user's judgment
about scientific metadata, naming ergonomics, or downloaded-file appearance.

Browser downloads do not currently create provenance records. Phase 6 deliberately
did not add a misleading “download completed” event because Streamlit artifact
generation cannot prove that the browser finished saving a file, and persistence
changes were outside the approved phase.
