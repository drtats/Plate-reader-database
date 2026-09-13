# ADR 0041: Significant-figure concentration matching in Growth Data Export

Status: accepted, 2026-09-13. Extends ADR 0040.

Users entered equivalent twofold dilution doses at different precision, such as
0.1875 and 0.19. Exact numeric matching placed these in different cultivation groups.
The user explicitly chose two significant figures for export matching.

The export UI defaults to two significant figures and offers exact, three, and four
figure alternatives. Public command/function defaults remain exact for compatibility.
A pure Decimal helper rounds only treatment doses, independently of global decimal
context, with ROUND_HALF_UP. Combination slots are normalized before sorting. All
other condition semantics, including scope, strain, medium, unit scales and unknown
values, remain unchanged. Saved cultivation keys are not regenerated or rewritten.

Both CSVs keep entered dose columns and add matching dose columns plus the precision
rule. Preview shows both dose summaries; the artifact signature includes precision.
This makes grouping reproducible without correcting source records. Zero stays zero,
small nonzero doses stay nonzero, and distinct twofold neighbors remain distinct.
This is deterministic rounding, not an estimate of the intended stock or dilution
series; values on opposite rounding boundaries may still need a different precision.

No schema migration or database writes are required. Tests cover rounding examples,
halfway cases, combinations, exact mode, invalid precision/nonfinite doses, original
value preservation, cross-plate cumulative replicates and stale download invalidation.
