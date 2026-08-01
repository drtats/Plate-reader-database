# Security policy

Do not report vulnerabilities with real credentials or laboratory data in a
public issue. Remove secrets immediately, rotate affected credentials, preserve
minimal evidence, and contact the repository owner through a private channel.

Security boundaries include OIDC identity, database-backed roles, parameterized
SQL, immutable raw-data/provenance triggers, bounded uploads, strict portable
validation, transactional imports, optimistic concurrency, soft deletion,
ignored secret/data files, CI secret scanning, and read-only rollback mode.

Real Turso and hosted authentication are not yet production-supported. Fake-cloud
mode provides no remote security assurance. Before hosted use, complete the
remote contract, anonymous denial, role matrix, backup/restore, logging, secret
scan, dependency review, and UAT gates in the administrator runbook.
