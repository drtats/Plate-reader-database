# Version and compatibility policy

- Application releases use semantic versioning. Pre-1.0 minor versions may add
  capabilities but do not silently reinterpret stored raw data.
- Migrations are ordered, append-only, checksum-verified, and forward-only.
  Upgrade only after a verified complete backup; rollback restores the prior
  database rather than reversing schema statements in place.
- Scientific algorithm identifiers are immutable. A result-changing update gets
  a new identifier, ADR, golden fixtures, and explicit recomputation; old
  revisions remain readable.
- Portable format version 1 is self-describing standard SQLite. Readers must
  reject unknown format/schema versions and executable objects. A future writer
  may add a new version only with migration/round-trip tests and a documented
  support window.
- Local, fake-cloud, hosted, macOS, and Windows modes share one schema and domain
  package. A release is not cross-mode compatible until repository and portable
  contract suites pass on its target adapter/platform.
- Legacy growth and MIC formats are accepted only through fingerprinted importers
  that report absent/defaulted fields and preserve originals byte-for-byte.

Version 0.1 supports schema v1, portable format v1, and the scientific identifiers
listed in `application/contracts.py`. Real Turso remote behavior becomes supported
only after its currently pending remote contract gate passes.
