# ADR-0046 Growth checkerboard layouts and CSV exports

Accepted 2026-11-10.

Growth layouts expose three treatments per well, each with a name, concentration
and unit. This matches the three condition slots already supported by the
cultivation registry. The primary treatment remains in the condition table.
Additional treatments use the existing numbered well JSON keys, with legacy
condition JSON available as a read fallback. The growth treatment resolver
recognizes numbered layout labels. A present canonical key takes precedence,
including an explicit clear. Data from a conflicting legacy alias can still be
inspected in the original metadata JSON.

Library summaries and search read well and condition metadata. Both analysis
CSVs share the resolved treatment fields. The default UI export includes
populated custom fields in observations and compact well metadata. Full metadata
provides registry details and original JSON for audit. The column contract and
v4 header mapping are recorded in [Growth tabular export v5](../contracts/GROWTH_TABULAR_EXPORT_V5.md).

Existing saved IDs require an explicit preview and save when newly recognized
drug combinations change their fingerprints. Reviewed condition splits preserve
the prior external IDs in history. All writes use the existing metadata
transaction and provenance paths. No schema migration is required.
