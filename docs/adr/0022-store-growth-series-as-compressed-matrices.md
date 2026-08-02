# ADR-0022: Store growth series as compressed matrices

- Status: accepted
- Date: 2026-08-02
- Owners: persistence owner
- Extends: ADR-0002, ADR-0004

## Context

A 96-well, 24-hour run sampled every ten minutes contains 13,920 logical
measurements. Storing each measurement as a row repeated plate, well, and
channel identifiers and maintained three indexes. A representative 126 KB CSV
therefore produced a 6.0 MB SQLite database and more than 13,000 Turso row
writes. Metadata was not the source of the growth.

## Decision

New growth imports store one immutable, compressed matrix per plate and channel
in `growth_series_chunks`. The matrix uses lossless IEEE-754 float64 values, a
null bitmap, a shared integer time axis, ordered plate positions, zlib
compression, and a SHA-256 integrity checksum. The repository expands this
physical representation to the existing logical measurement dictionaries, so
application and domain services remain storage-independent.

The original `growth_measurements` table remains readable for existing
databases. A plate may use either representation, never both. Version-1
portable exports and complete backups continue to materialize canonical
row-based measurements so older builds and standard SQLite tools remain usable.
Portable imports prefer compact storage and fall back to legacy rows only for a
sparse, nonrectangular artifact that cannot be represented as a matrix.

## Consequences

The measured full synthetic run now occupies 397,312 bytes and saves in about
0.05 seconds locally, while loading all 13,920 logical measurements in about
0.01 seconds. New Turso imports write one raw-data record per channel instead of
one per measurement. Direct SQL consumers must use the repository or a portable
export to see logical rows. Compact records are immutable and checksum-verified
when read.

## Verification

Codec tests cover lossless null handling, invalid axes, and tamper detection.
Repository tests cover compact writes, legacy reads, immutability, streaming,
portable round trips, and backup/restore. The persistence regression test
requires 13,920 decoded observations, one physical chunk, zero new legacy rows,
and a database smaller than 1 MB.
