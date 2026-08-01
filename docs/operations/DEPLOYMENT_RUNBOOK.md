# Deployment and migration runbook

Status: fake-cloud procedure verified; real Turso/OIDC deployment pending user
configuration.

## Local and fake-cloud migration

Apply numbered migrations explicitly before starting a pilot:

```bash
uv run --no-sync python scripts/migrate_database.py \
  .data/plate-reader.sqlite --backend fake-cloud
```

The command verifies migration checksums and prints every applied version. It is
safe to run again. Do not edit an applied migration; add the next numbered SQL
file.

## Real-cloud prerequisites (not yet executed)

1. Create the private GitHub repository and enable required CI checks.
2. Create separate Turso development and production databases. Never test
   destructive behavior against production.
3. Use the implemented official Python `libsql` adapter. Fake-cloud tests do not
   prove network transaction behavior, so the opt-in live contract remains a
   required gate.
4. Select Google Identity or Microsoft Entra and create an OIDC client with the
   deployed app URL plus `/oauth2callback` as its redirect URL.
5. Copy `.streamlit/secrets.example.toml` into Streamlit Community Cloud secrets
   and replace placeholders there. Never create a committed secrets file.
6. Provision the first admin email out of band, then use audited admin operations
   for later role changes.
7. Deploy the app as private and verify anonymous denial before importing any
   representative synthetic run.

## Real Turso commands

Install and authenticate the Turso CLI, then create a disposable development
database. Follow Turso's current CLI output rather than placing either credential
in Git:

```bash
turso db create plate-reader-development
export TURSO_DATABASE_URL="$(turso db show plate-reader-development --url)"
export TURSO_AUTH_TOKEN="$(turso db tokens create plate-reader-development)"
```

Apply migrations, create the first database-authorized administrator, and inspect
logical counts:

```bash
make turso-migrate
uv run --no-sync python scripts/manage_turso.py bootstrap-admin \
  scientist@example.org --display-name "Initial Administrator"
make turso-status
```

The token is accepted only from the process environment or Streamlit secret
storage, never as a command-line argument. Bootstrap refuses to run once any user
exists. For the disposable live contract database, use separate test-only values:

```bash
export TURSO_TEST_DATABASE_URL="$TURSO_DATABASE_URL"
export TURSO_TEST_AUTH_TOKEN="$TURSO_AUTH_TOKEN"
make test-remote
```

The live contract deliberately requires an empty isolated database and leaves its
synthetic contract run behind for inspection. Never point it at production.

For hosted operation set `PLATE_READER_ENV=production`,
`PLATE_READER_STORAGE_MODE=cloud`, and `PLATE_READER_OIDC_PROVIDER=google` (or
`microsoft`) in Streamlit configuration, then copy the Turso and `[auth]` values
from `.streamlit/secrets.example.toml` into Community Cloud secret storage.

Streamlit's native OIDC flow uses `st.login()`, `st.user`, and `st.logout()`;
authentication identifies the user while the database remains the authorization
source. See the [official Streamlit authentication guide](https://docs.streamlit.io/develop/concepts/connections/authentication).

## Pilot checks

- CI is green from a clean checkout.
- The app shows `production` and `cloud`, never `fake-cloud`.
- Anonymous access stops at login before a database query.
- An unregistered or inactive identity is denied.
- Viewer, editor, and admin accounts pass the role matrix.
- A full synthetic run imports, reloads, plots, exports, and restores.
- Two browser sessions produce a visible optimistic-concurrency conflict.
- Cold start and core operation timings are recorded in the Phase 5 report.
- Turso usage is recorded before and after the synthetic import.
- No token or OIDC value appears in application logs or downloaded artifacts.

## Rollback

Set this deployment secret and reboot the app:

```text
PLATE_READER_WRITES_ENABLED=false
```

The application actor is then constrained to viewer capability and every write
service rejects commands even if a write control remains visible. Confirm reads
and portable export, then investigate. If reads are unsafe, stop the deployment.
Local mode and untouched legacy databases remain available.

Do not move real laboratory data to a public app to work around Streamlit's
private-app limit. Transition the existing private slot deliberately or use the
local server/Cloudflare Tunnel mode until a safe slot is available.
