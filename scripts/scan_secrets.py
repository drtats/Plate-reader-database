"""Fail when tracked files contain likely credentials or unsafe secret filenames."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_CONFIG_SUFFIXES = {".env", ".json", ".toml", ".yaml", ".yml"}
_SAFE_MARKERS = ("replace", "example", "dummy", "fake", "test", "changeme", "${", "$(")
_UNSAFE_PATHS = {".env", ".streamlit/secrets.toml"}
_DIRECT_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access key", re.compile("AKIA" + r"[A-Z0-9]{16}")),
    ("GitHub token", re.compile("gh" + r"[pousr]_[A-Za-z0-9]{30,}")),
    ("Google OAuth client secret", re.compile("GOCSPX" + r"-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile("xox" + r"[aboprs]-[A-Za-z0-9-]{20,}")),
)
_CONFIG_ASSIGNMENT = re.compile(
    r"(?i)(?:auth[_-]?token|client[_-]?secret|cookie[_-]?secret|api[_-]?key|password)"
    r"\s*[=:]\s*[\"']?([^\"'\s#]{8,})"
)


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    kind: str


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    findings: list[Finding] = []
    for relative in tracked_files(root):
        if relative in _UNSAFE_PATHS:
            findings.append(Finding(relative, 0, "tracked secret file"))
            continue
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(relative, text))
    if findings:
        print("Potential committed secrets found (values are intentionally redacted):")
        for finding in findings:
            location = finding.path if finding.line == 0 else f"{finding.path}:{finding.line}"
            print(f"  {location}: {finding.kind}")
        raise SystemExit(1)
    print("Tracked-file secret scan passed; no credential patterns found.")


def tracked_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(item.decode() for item in result.stdout.split(b"\0") if item)


def scan_text(relative_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    suffix = Path(relative_path).suffix.casefold()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in _DIRECT_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(relative_path, line_number, kind))
        if suffix in _CONFIG_SUFFIXES:
            for match in _CONFIG_ASSIGNMENT.finditer(line):
                value = match.group(1).casefold()
                if not any(marker in value for marker in _SAFE_MARKERS):
                    findings.append(Finding(relative_path, line_number, "configured secret value"))
    return findings


if __name__ == "__main__":
    main()
