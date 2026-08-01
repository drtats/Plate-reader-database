from __future__ import annotations

from scripts.scan_secrets import scan_text


def test_secret_scan_detects_direct_tokens_without_echoing_values() -> None:
    token = "gh" + "p_" + "A" * 35
    findings = scan_text("settings.py", f"value = '{token}'")
    assert [(finding.line, finding.kind) for finding in findings] == [(1, "GitHub token")]
    assert token not in repr(findings)


def test_secret_scan_detects_real_config_values_and_allows_placeholders() -> None:
    assert scan_text("settings.toml", 'client_secret = "real-secret-value-123"')
    assert not scan_text("settings.example.toml", 'client_secret = "REPLACE_WITH_SECRET"')
    assert not scan_text("settings.toml", 'auth_token = "${TURSO_AUTH_TOKEN}"')


def test_secret_scan_detects_private_keys() -> None:
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    findings = scan_text("certificate.txt", f"{marker}\nredacted")
    assert findings[0].kind == "private key"
