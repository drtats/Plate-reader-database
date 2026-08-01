from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "import_legacy_growth.py"
FIXTURE = ROOT / "tests" / "fixtures" / "legacy" / "growth_v4.sqlite"


def test_batch_cli_dry_run_creates_no_destination_then_commit_imports(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "destination.sqlite"
    report_path = tmp_path / "report.json"
    shutil.copyfile(FIXTURE, source)

    dry_run = run_cli(target, source, "--report", str(report_path))

    assert dry_run.returncode == 0, dry_run.stderr
    assert not target.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["files"][0]["report"]["runs"][0]["status"] == "ready"

    commit = run_cli(target, source, "--commit", "--bootstrap-editor")

    assert commit.returncode == 0, commit.stderr
    assert target.is_file()
    committed = json.loads(commit.stdout)
    assert committed["mode"] == "commit"
    assert committed["files"][0]["report"]["runs"][0]["status"] == "imported"


def run_cli(target: Path, source: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(target), str(source), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
