# Phase 5 fake-cloud readiness report

Status: local prerequisites implemented; real-cloud exit gate open.

## Completed locally

- cached process-level connections and per-session growth views invalidated by a
  lightweight metadata/current-revision token;
- database-backed viewer/editor/admin checks with inactive-user and identity
  mismatch denial;
- verified OIDC claim parsing that ignores any provider-supplied role claim;
- actor IDs on growth imports, metadata/layout edits, revisions, and provenance;
- optimistic metadata and layout concurrency inside the write transaction;
- atomic simulated-cloud import failure tests;
- explicit migration, complete backup, and complete restore commands;
- a tested read-only rollback switch;
- CI secret scanning through Gitleaks and placeholder-only secret examples.

## Capacity estimate

The measured full growth run occupies about 6.3 MB locally and writes 13,920 raw
rows plus roughly 200 supporting rows. At the current Turso free allowances of
5 GB storage, 10 million rows written per month, and 500 million rows read per
month, rough upper bounds are:

- about 790 full runs by storage before safety margin, indexes, revisions, and
  backups;
- about 700 full-run imports per month by row writes;
- about 35,000 complete raw-run loads per month by row reads.

These are planning estimates, not quotas promised by this application. Real
usage must be recorded after representative imports because Turso accounting and
additional revisions/searches affect totals. See [Turso pricing](https://turso.tech/pricing)
and [usage documentation](https://docs.turso.tech/help/usage-and-billing).

## Still required for the exit gate

- create Turso development/production databases;
- run the implemented official Python `libsql` adapter contract against the
  isolated remote DB;
- configure and exercise Google or Microsoft OIDC;
- create/push the GitHub repository and observe hosted CI;
- deploy the private Streamlit pilot;
- record Community Cloud cold-start/list/load/save/plot timings;
- run the cloud backup/restore drill and verify real usage;
- test anonymous denial and every role in the deployed app.

No real Turso or OIDC credential has been created or used in this phase.
