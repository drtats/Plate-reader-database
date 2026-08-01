# Phase 7 MIC module and migration evidence

Status: local and fake-cloud implementation complete, 2026-08-01. Real Turso
credentials and the final hosted cutover are intentionally deferred.

## Delivered workflow

- A strict five-step MIC import wizard previews a long-format 96-well plate,
  records experiment metadata, stages optional per-well edits, and commits raw
  readings, layout, the first analysis revision, source identity, and provenance
  atomically.
- The shared Plate Library and MIC result search use bounded server-side queries
  with limit/offset pagination and schema indexes.
- The MIC workspace shows raw-value and growth-call plate maps, metadata,
  editable well layout, current and historical revisions, endpoint results,
  manual review state, administrator lock/delete/restore controls, portable
  export, and provenance.
- Threshold or layout changes create immutable new derived revisions; stored raw
  OD readings are never updated.
- Viewer/editor/admin permissions are enforced in application services and can
  be exercised locally with `PLATE_READER_DEV_ROLE`. Production mode still
  requires authenticated identity.

## Legacy importer

`scripts/import_legacy_mic.py` fingerprints supported legacy SQLite schemas,
opens sources read-only, hashes every original before and after inspection,
validates complete 8x12 geometry, maps metadata and custom labels explicitly,
recomputes canonical `mic-endpoint/1.0.0` calls/results, and reports every
derived difference. Dry-run is the default. Commit mode requires an existing
authorized target user unless the operator explicitly requests the safe
bootstrap editor.

The importer does not infer absent values silently. A null legacy plate format
is inferred as 96 only after all 96 canonical wells validate; null method fields
receive documented defaults and warnings. Derived differences block commit
unless the operator deliberately supplies `--allow-derived-differences`.

## Verification evidence

Synthetic fixture:

- source: `tests/fixtures/legacy/mic_legacy.sqlite`
- SHA-256: `d6eff47c2325f3b2c4ee358f5102f5b23df28b7baa7529077571963170423af4`
- expected: one plate, 96 wells, four MIC result groups
- checks: exact counts, raw values, flags, methods, labels, and canonical results

Read-only inspection of the existing MIC application database:

- source: `MIC analysis tool/mic_analysis.db`
- original SHA-256:
  `afdd7f4590d18aa9d71677621a1f8719a0f9b43f00a11bebd1f9f577b25a5459`
- detected: eight plates, 768 wells, 64 legacy result groups
- recomputed: 64 canonical result groups with zero derived differences
- staging-copy commit: eight plates, 768 wells, 768 raw MIC readings, 64 result
  groups, eight provenance events, and `PRAGMA integrity_check = ok`
- original SHA-256 after dry-run/staging work: unchanged

Full repository gate:

```text
240 passed, 1 skipped
combined line/branch coverage: 90.29%
Ruff: pass
mypy: pass
```

The skipped test requires an optional real remote adapter environment; the local
and fake-cloud adapters run the same reusable repository contracts.

## Deferred production cutover

The real Turso database, production authentication configuration, Streamlit
Community Cloud deployment, pre-cutover restore point, and final controlled
migration remain external release gates. No real credentials were requested or
stored, and no legacy database was modified. Complete these steps only after the
user creates the Turso service and GitHub/Streamlit deployment.
