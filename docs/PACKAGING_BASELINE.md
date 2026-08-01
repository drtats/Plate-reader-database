# Standalone packaging baseline

Measured 2026-08-01 on macOS arm64 with Python 3.12.13 and PyInstaller 6.21.0.

| Measurement | Result |
| --- | ---: |
| Clean macOS `.app` build time | 35.934 s |
| Non-symlink payload bytes | 161,354,218 B |
| Non-symlink payload size | 161.354 MB |
| On-disk bundle (`du -sh`) | 161 MB |
| Frozen server health startup | 0.488 s |

The first conservative bundle was 401.789 MB. Excluding unused PyArrow,
Streamlit demo/testing modules, pytest, matplotlib, and TensorFlow reduced the
payload by 59.8%. The application does not use Streamlit Arrow-backed dataframe
widgets; its tables are bounded record/card renderings and plots are Plotly.
The cloud-only `libsql` and OIDC cryptography packages are also excluded from the
offline bundle and imported only by hosted mode.

The frozen smoke test validates:

1. first-run configuration in an isolated data directory;
2. schema initialization through bundled migrations and `pyturso`;
3. verified complete backup;
4. verified restore into a new selected database; and
5. a healthy packaged Streamlit server on a temporary localhost port.

The Windows workflow emits the same JSON measurements. Its first measured result
remains pending until the repository is published and the packaging workflow is
run on GitHub.
