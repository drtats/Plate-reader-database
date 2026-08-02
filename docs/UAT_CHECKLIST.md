# User-acceptance pilot checklist

Run only on copied or anonymized data. Record tester, app version, adapter,
browser/OS, date, and evidence for each item.

## Growth workspace feedback workflow

- [ ] Start the app by double-clicking `Start Plate Reader.command` and import an
      anonymized Growth CSV.
- [ ] Complete rich metadata and confirm Layout keeps synchronized **96-well
      plate** and **Full well table** views.
- [ ] Build display names from ordered metadata fields; preview before applying.
- [ ] Download, edit in Excel, and upload a partial display-name CSV; save and
      confirm names in both Layout views after reload.
- [ ] Select wells physically and with metadata add/remove filters without
      reopening Layout.
- [ ] Inspect earliest, middle, and final heatmap timepoints for a channel; compare
      raw and background-corrected values.
- [ ] Render rainbow and categorical plots; verify repeated display names remain
      separate physical-well curves.
- [ ] Download PNG, PDF, and selected-data CSV. Confirm the CSV contains exactly
      the visible selected series, channels, and correction state.
- [ ] Read **Background history** and **Activity log**, then open both technical
      detail expanders and confirm IDs/payloads remain available.
- [ ] Restart the app and confirm saved metadata, layout, display names, and
      default plot selection persist.

Portable import and MIC are outside this Growth feedback UAT and should not be
changed merely to make these checks pass.

## Full pilot

- [ ] Import representative plate-reader exports with and without explicit time.
- [ ] Confirm layout, metadata, blank groups, sampled curves, and background QC.
- [ ] Edit metadata/layout in two sessions and observe safe conflict handling.
- [ ] Export and re-import growth and MIC plates with expected collision mapping.
- [ ] Import a copied legacy growth library and reconcile counts/raw hashes.
- [ ] Import a copied legacy MIC library and reconcile results/warnings/states.
- [ ] Exercise viewer, editor, and admin permissions including soft delete/restore.
- [ ] Create a complete backup, restore to a clean target, and sample both assays.
- [ ] Record common operation timings and Turso usage/quota movement.
- [ ] Confirm no credential, identifiable data, or unrelated plate appears in
      logs, exports, or support artifacts.
- [ ] Record defects, severity, owner, and retest result; no critical issue may
      remain open at cutover.
