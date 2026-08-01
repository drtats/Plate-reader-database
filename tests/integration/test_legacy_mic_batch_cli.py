from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "import_legacy_mic.py"
FIXTURE = ROOT / "tests" / "fixtures" / "legacy" / "mic_legacy.sqlite"


def test_mic_batch_dry_run_creates_no_target_then_commit_imports(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    shutil.copyfile(FIXTURE, source)

    dry = run_cli(target, source)

    assert dry.returncode == 0, dry.stderr
    assert not target.exists()
    assert json.loads(dry.stdout)["files"][0]["report"]["plates"][0]["status"] == "ready"

    commit = run_cli(target, source, "--commit", "--bootstrap-editor")

    assert commit.returncode == 0, commit.stderr
    assert target.is_file()
    report = json.loads(commit.stdout)
    assert report["files"][0]["report"]["plates"][0]["status"] == "imported"


def run_cli(target: Path, source: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(target), str(source), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
