# ADR 0045: Reduce repeated database calls in Growth workflows

Status: accepted, 2026-09-14.

Remote latency makes repeated small queries expensive even when local execution is
fast. Reduce redundant work at existing operation boundaries without caching user
permissions or changing cultivation identity, background freshness, or raw data.

Cloud context uses its required fresh identity lookup to detect an expired Hrana
stream instead of issuing a separate `SELECT 1` on every rerun. Only that read is
retried, once, after reopening the connection. Write services are never replayed.
Hosted identity provisioning preserves an existing user's role and active status;
the retry reconnect does not provision users.

`update_well_layout` prefetches selected wells and condition state, then issues
parameterized sparse CASE updates in chunks of at most 900 bound parameters.
Repeated edits to one position retain sequential semantics, including explicit
clears and Growth primary-condition overrides. Validation precedes writes, and
service transaction boundaries continue to provide rollback. This is true SQL
batching rather than assuming that `executemany` is one remote request.

Cultivation saves read the selected metadata and complete numbering projection once
inside the write transaction, reuse them for validation and persistence, and retain
fresh authorization before and inside that transaction. Preview and saved-ID
collision checks remain intact.

`LoadGrowthRunService.load_for_tabular_export` authorizes once per export request
and omits unused provenance. The normal single-run loader still includes provenance.
Each run retains the same snapshot, current revision, and stale-background checks.
The export's custom-column projection shares the request's authorization.

## Reproduction and evidence

Run `.venv/bin/python scripts/benchmark_growth_roundtrips.py` from the repository.
It creates and removes a synthetic 96-well fake-cloud database; it never opens a
laboratory database. Counts exclude fixture setup and report SQL statements,
not measured network packets or real Turso elapsed time.

| Operation | Before | After |
| --- | ---: | ---: |
| Warm cloud context: identity and health SQL | 2 | 1 |
| Update cultivation metadata on 96 wells | 192 | 2 |
| Full cultivation save for 96 wells | 202 | 9 |
| Two-run CSV export: user lookups | 3 | 1 |
| Two-run CSV export: provenance reads | 2 | 0 |

The benchmark also records local execution time without projecting a remote speedup.
Contract tests cover chunk bounds, duplicate positions, Growth/MIC conditions,
constraint failures, and rollback. Integration tests verify export bytes match the
original loader and deny newly inactive users before loading data. Cloud tests cover
fresh authorization, the single retry, and preservation of stored roles.
