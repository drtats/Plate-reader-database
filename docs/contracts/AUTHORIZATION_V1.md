# Authorization contract v1

Authentication identifies a person; the `users` table determines their current
application role. All cloud data pages require authentication. Local development
uses a visibly labeled development actor and that bypass is rejected in
production configuration.

| Capability | Viewer | Editor | Admin |
| --- | :---: | :---: | :---: |
| View/search/export non-deleted data | yes | yes | yes |
| Import runs and create analyses | no | yes | yes |
| Edit experiment, plate, and layout metadata | no | yes | yes |
| Recompute revisioned analyses | no | yes | yes |
| Mark MIC manually checked | no | yes | yes |
| Finalize a draft | no | yes | yes |
| Soft-delete or restore | no | no | yes |
| Lock/unlock deletion | no | no | yes |
| Manage users, roles, and templates | no | no | yes |
| Apply migrations | no | no | deployment/admin operation |

Rules:

1. Every command carries an `Actor`; repositories never infer one from global UI
   state.
2. Every committed write appends a `provenance_events` record in the same
   transaction.
3. Role checks occur in application services before repository mutation. UI
   hiding is convenience, not authorization.
4. Inactive users are denied regardless of stored role.
5. Export is treated as a data read and logged. Imported archives never create or
   elevate active users.
6. Optimistic concurrency uses `updated_at`; stale edits return a conflict rather
   than overwriting another session.
7. Raw observations and provenance cannot be updated or physically deleted by
   any role.

OIDC provider details and real identity claims are verified in Phase 5. The
fake-cloud adapter uses deterministic fake identities only in development/test.
