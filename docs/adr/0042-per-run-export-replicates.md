# ADR 0042: Restart export cultivation replicates within each run

Status: accepted, 2026-09-14. Supersedes cumulative export numbering in ADR 0039.

The user no longer needs cumulative replicate numbers across experiments because the
cultivation ID already includes an experiment number. Export generation now starts
R1 for each matching-condition group within each physical run/plate. Another run
starts R1 again, and its experiment number distinguishes its cultivation IDs.

The selected-run planner keys counters and matching counts by plate and condition.
It includes explicit export fields and only that plate's saved matching fields, so
adding an unrelated plate cannot change existing assignments. Ordering within a run
uses physical well position, including projections without row/column indices.
Two-significant-figure dose matching and micro-unit spelling normalization remain
available within each run. Local replicate labels and original concentrations remain
separate, unchanged values.

The effective metadata mode is export_run. Both CSVs share the generated R values
and IDs; preview matching counts are local to the run. The UI no longer describes
cumulative numbering, and its artifact signature version hides old cached downloads.
The saved-ID and saved library condition-numbering paths retain compatibility.
No schema migration, reservation or database write is introduced.

Regression checks cover two matching wells on each of two runs (R1/R2 and R1/R2),
unchanged assignments when exporting either run alone, separate run field policies,
unit/precision normalization within runs, stable physical ordering, CSV joins and
read-only persisted data.
