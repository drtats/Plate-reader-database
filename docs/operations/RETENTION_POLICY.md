# Backup, export, and deletion retention policy

This repository defines safe defaults; the data owner must approve final periods
before production use.

- Raw experimental observations are retained unless the data owner approves a
  documented deletion after verified archival. Application soft delete is not
  physical erasure.
- Keep at least three verified complete backups across two failure domains,
  including one pre-release/pre-migration backup. Test a restore quarterly and
  before every schema or legacy cutover.
- Suggested internal schedule: daily backups for 14 days, weekly for 12 weeks,
  monthly for 12 months, plus release/cutover backups until the legacy retention
  period ends.
- Portable exports are user-selected transfer artifacts, not authoritative
  backups. Delete temporary exports from shared download folders after 30 days
  unless a project record requires longer retention.
- Never place credentials, unrelated users, or unrelated plates in an export.
- Expiration deletes only after artifact identity, owner approval, and another
  verified copy are recorded. Prefer recoverable trash/quarantine before final
  destruction where the platform supports it.
- Security/legal/laboratory-record obligations override these defaults and must
  be recorded before hosted production use.

The untouched legacy applications/databases remain read-only recovery references
through the pilot and agreed retention period. Do not delete or mutate them from
this project.
