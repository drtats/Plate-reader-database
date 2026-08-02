# Streamlit Community Cloud and Turso setup

This runbook moves the repository from fake-cloud testing to a private hosted
app backed by Turso. Use synthetic data until every acceptance check passes.

## 1. Verify the repository

From the repository root:

```bash
uv sync --all-groups
make check
```

Deployment uses Python 3.12, root entrypoint `app.py`, `uv.lock`, and
`.streamlit/config.toml`. Never commit `.env`, `.streamlit/secrets.toml`, a
database token, or an OIDC client secret. The committed
`.streamlit/secrets.example.toml` contains placeholders only.

## 2. Publish to GitHub

Create a GitHub repository, commit the app, and push the intended deployment
branch. Streamlit Community Cloud deploys from GitHub and watches that branch.
A private repository is appropriate for this laboratory app; grant Streamlit
access when connecting the GitHub account.

Before pushing:

```bash
git status
make secret-scan
```

## 3. Create Turso

Install and authenticate the Turso CLI on the administrator's computer:

```bash
brew install tursodatabase/tap/turso
turso auth signup
turso db create plate-reader-production
```

Retrieve a database-scoped URL and write token. Store the token in a password
manager; do not save it in Git:

```bash
turso db show plate-reader-production --url
turso db tokens create plate-reader-production
```

Expose those values as `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` in a trusted
terminal session for one-time administration, then run:

```bash
make turso-migrate
make turso-status
.venv/bin/python scripts/manage_turso.py bootstrap-admin \
  YOUR_GOOGLE_EMAIL --display-name "Your Name"
```

`bootstrap-admin` works only while the users table is empty. Use the exact email
returned by OIDC. Keep a separate disposable Turso database for remote contract
tests; never point tests at production.

## 4. Configure Google OIDC

In Google Cloud, create or select a project, configure the OAuth consent screen,
and create an OAuth **Web application** client. Its authorized redirect URI is:

```text
https://YOUR-APP.streamlit.app/oauth2callback
```

Keep the client ID and client secret. Generate a strong independent cookie
secret. Authentication identifies a user; this app's `users` table separately
controls viewer/editor/admin authorization.

If the Google app remains in Testing, add every intended tester. Publish or
apply an organization restriction only after synthetic-data checks pass.

## 5. Deploy on Streamlit Community Cloud

1. Sign in at [share.streamlit.io](https://share.streamlit.io) and connect GitHub.
2. Choose **Create app**, then the repository, branch, and `app.py`.
3. Choose Python **3.12** in Advanced settings.
4. Choose a stable app subdomain before finalizing OIDC.
5. Paste a completed copy of `.streamlit/secrets.example.toml` into **Secrets**.

Required hosted secrets:

```toml
PLATE_READER_ENV = "production"
PLATE_READER_STORAGE_MODE = "cloud"
PLATE_READER_OIDC_PROVIDER = "google"
PLATE_READER_WRITES_ENABLED = "true"
TURSO_DATABASE_URL = "libsql://YOUR-DATABASE.turso.io"
TURSO_AUTH_TOKEN = "YOUR_DATABASE_TOKEN"

[auth]
redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
cookie_secret = "YOUR_RANDOM_COOKIE_SECRET"

[auth.google]
client_id = "YOUR_GOOGLE_CLIENT_ID"
client_secret = "YOUR_GOOGLE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

The redirect URI must match exactly in Streamlit secrets and Google Cloud.

## 6. Acceptance checks

- The header reports `production` and `cloud`, never `fake-cloud`.
- An anonymous browser stops at Sign in.
- The bootstrapped admin signs in; an unregistered email is denied.
- Import one synthetic 24-hour run and reopen it from the library.
- Metadata/layout edits show before/after values in Activity log.
- Render labeled curves and download PDF, long CSV, and wide CSV.
- Reboot the app and confirm the run still exists in Turso.
- Create a verified backup from a trusted administrator machine.
- Confirm credentials do not appear in logs or artifacts.

Do not upload irreplaceable experimental data until all checks pass.

## 7. Operations and rollback

Before risky updates, follow `docs/operations/BACKUP_RESTORE.md`. To stop writes
without removing read access, set this hosted secret and reboot:

```toml
PLATE_READER_WRITES_ENABLED = "false"
```

Rotate a Turso token by creating a replacement, updating Streamlit Secrets,
rebooting and testing, then revoking the old token. Streamlit's container disk is
not the durable database; Turso is.

## Official references

- [Deploy on Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Streamlit secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Streamlit Google authentication](https://docs.streamlit.io/develop/tutorials/authentication/google)
- [Turso CLI](https://docs.turso.tech/cli/introduction)
- [Turso Python quickstart](https://docs.turso.tech/sdk/python/quickstart)
- [Turso database tokens](https://docs.turso.tech/cli/db/tokens/create)
