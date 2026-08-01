"""Build and measure the platform-native standalone distribution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            str(root / "packaging" / "plate_reader.spec"),
        ],
        cwd=root,
        env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(root / ".tools" / "pyinstaller")},
        check=True,
    )
    elapsed = time.perf_counter() - started
    artifact = _artifact(root)
    size = sum(
        path.stat().st_size
        for path in artifact.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    report = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "build_seconds": round(elapsed, 3),
        "artifact_bytes": size,
        "artifact_megabytes": round(size / 1_000_000, 3),
    }
    report_path = root / "dist" / "standalone-build-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if not artifact.is_dir() or not _executable(artifact).is_file():
        raise RuntimeError(f"Standalone executable missing: {_executable(artifact)}")


def _artifact(root: Path) -> Path:
    if sys.platform == "darwin":
        return root / "dist" / "PlateReaderDatabase.app"
    return root / "dist" / "PlateReaderDatabase"


def _executable(artifact: Path) -> Path:
    if sys.platform == "darwin":
        return artifact / "Contents" / "MacOS" / "PlateReaderDatabase"
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return artifact / f"PlateReaderDatabase{suffix}"


if __name__ == "__main__":
    main()
