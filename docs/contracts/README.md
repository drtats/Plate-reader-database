# Frozen application contracts

- `LEGACY_BEHAVIOR.md`: observed growth/MIC behavior and intentional changes.
- `SCHEMA_V1.md`: normalized schema, lifecycle rules, indexes, and capacity gate.
- `AUTHORIZATION_V1.md`: role/capability matrix and audit requirements.
- `PORTABLE_FORMAT_V1.md`: standard SQLite export, validation, collision, and
  checksum protocol.
- `DOMAIN_V1.md`: validated growth/MIC algorithms, formulas, and intentional
  legacy corrections.
- `GROWTH_TABULAR_EXPORT_V1.md`: historical mixed run/well metadata export contract.
- `GROWTH_TABULAR_EXPORT_V2.md`: current multi-run Growth observation export and
  homogeneous one-row-per-run experiment metadata contract.

The executable counterparts are the typed DTO/repository protocols under
`src/plate_reader/application`, SQL under `migrations`, and golden fixtures/tests
under `tests`. After Phase 1 exits, changing these contracts requires an ADR.
