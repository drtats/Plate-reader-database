# Persistence performance baseline

## Compact-series baseline

Measured on 2026-08-02 after migration 0002. The same 96-well, 145-timepoint
workload still exposes 13,920 logical measurements, but stores one compressed
raw record for its single channel.

| Adapter | Save median | Load median | Database size |
| --- | ---: | ---: | ---: |
| `pyturso` | 0.047 s | 0.012 s | 397,312 B |
| `fake-cloud` | 0.049 s | 0.009 s | 397,312 B |

This is about a 94% reduction from the 6.0 MB row-based baseline. The current
regression budget is 1 MiB. Turso usage must still be checked after deployment,
but raw writes are now one record per channel rather than one per measurement.

## Phase 3 baseline

Measured on 2026-08-01 on arm64 macOS 26.2 with Python 3.12.13 and SQLite
3.53.1. The workload is a deterministic 96-well growth run sampled every 10
minutes from 0 through 24 hours: 145 timepoints and 13,920 immutable raw rows.
Each result below is five fresh-database repetitions.

| Adapter | Save median | Save p95 | Load median | Load p95 | File p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `pyturso` | 0.267 s | 0.279 s | 0.025 s | 0.027 s | 6,291,456 B |
| `fake-cloud` | 0.106 s | 0.107 s | 0.019 s | 0.021 s | 6,316,032 B |

The original repeatable regression budgets were 1.5 seconds to save, 0.25 seconds
to load, and 8 MiB for the database. These were intentionally wider than the observed
local p95 values to tolerate CI and filesystem variability. A failure is a signal
to investigate and remeasure; budgets must not be silently raised.

Run the harness with:

```bash
make benchmark
```

This baseline measures application parsing, one atomic repository transaction,
and loading the complete plate snapshot. It is not a Turso network benchmark;
real-cloud latency is deferred until credentials are available.

## Phase 9 shared-library workflow baseline

Measured on the same machine with one shared fake-cloud database containing 20
complete growth runs and 40 MIC plates: 60 experiments, 5,760 wells, 278,400
growth measurements, and 3,840 MIC readings.

| Operation | Result |
| --- | ---: |
| Growth import median / p95 | 0.109 / 0.130 s |
| MIC import median / p95 | 0.008 / 0.009 s |
| Indexed 25-run library page | 0.000216 s |
| Indexed 100-result MIC search | 0.000714 s |
| Complete growth plate load | 0.018872 s |
| 12 growth curves + endpoint heatmap | 0.078349 s |
| Complete MIC plate load | 0.000952 s |
| MIC heatmaps + result dot plot | 0.031100 s |
| One growth + one MIC portable export | 0.146798 s |
| Complete 124.8 MB database backup | 2.657960 s |

The database occupied 124,780,544 bytes, or about 6.239 MB per full growth run
with shared schema/MIC overhead. A one-run `dbstat` sample attributed about 1.47
MB to raw rows and about 4.50 MB to the primary-key, elapsed-time uniqueness, and
load-order indexes. Those measurements motivated the compact-series migration
described above.

As of 2026-08-01, Turso's Free plan advertises 5 GB storage, 500 million monthly
rows read, and 10 million monthly rows written. At the measured local density,
5 GB is roughly 800 full growth runs before safety margin, while 10 million writes
is roughly 700 new full runs in one month. See [Turso pricing](https://turso.tech/pricing)
and [usage accounting](https://docs.turso.tech/help/usage-and-billing); remote
storage layout and billing must be measured after real Turso is configured.

Operational thresholds:

- review capacity at 500 full growth runs or 3 GB, whichever comes first;
- alert at 60%, 80%, and 90% of the active Turso storage allowance;
- archive only through verified portable export/backup and an approved retention
  decision—never delete raw data merely to silence a quota;
- if projected use exceeds the free allowance, compare the low-cost paid tier
  with an ADR for compressed immutable raw blocks or object-storage archives.

Run the repeatable shared-library harness with `make benchmark-workflows`. CI also
enforces smaller, deliberately generous operation and size regression budgets in
`tests/integration/test_workflow_scale.py`.
