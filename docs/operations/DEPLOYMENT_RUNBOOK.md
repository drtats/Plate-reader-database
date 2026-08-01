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
3. Implement and contract-test the `turso_serverless` adapter before adding
   credentials. Fake-cloud tests do not prove network transaction behavior.
4. Select Google Identity or Microsoft Entra and create an OIDC client with the
   deployed app URL plus `/oauth2callback` as its redirect URL.
5. Copy `.streamlit/secrets.example.toml` into Streamlit Community Cloud secrets
   and replace placeholders there. Never create a committed secrets file.
6. Provision the first admin email out of band, then use audited admin operations
   for later role changes.
7. Deploy the app as private and verify anonymous denial before importing any
   representative synthetic run.

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
