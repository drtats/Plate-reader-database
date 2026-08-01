# User-acceptance pilot checklist

Run only on copied or anonymized data. Record tester, app version, adapter,
browser/OS, date, and evidence for each item.

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
