"""Smoke-test a built standalone executable without starting a browser."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    executable = _executable(root)
    with tempfile.TemporaryDirectory(prefix="plate-reader-package-smoke-") as temporary:
        result = subprocess.run(
            [str(executable), "--data-dir", temporary, "info"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(result.stdout)
        if payload["config_version"] != 1:
            raise RuntimeError("Unexpected desktop configuration version")
        if Path(payload["data_directory"]) != Path(temporary).resolve():
            raise RuntimeError("Packaged launcher ignored its selected data directory")
        subprocess.run(
            [str(executable), "--data-dir", temporary, "init"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        database = Path(payload["database_path"])
        backup = Path(temporary) / "backups" / "smoke-backup.sqlite"
        restored = Path(temporary) / "smoke-restored.sqlite"
        subprocess.run(
            [str(executable), "--data-dir", temporary, "backup", str(backup)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                str(executable),
                "--data-dir",
                temporary,
                "--database",
                str(database),
                "restore",
                str(backup),
                "--destination",
                str(restored),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        if not database.is_file() or not backup.is_file() or not restored.is_file():
            raise RuntimeError("Packaged database backup/restore smoke artifacts are incomplete")
        port = _available_port()
        started = time.perf_counter()
        server = subprocess.Popen(
            [
                str(executable),
                "--data-dir",
                temporary,
                "run",
                "--no-browser",
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_health(server, port)
            startup_seconds = time.perf_counter() - started
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    print(f"Packaged launcher and server smoke test passed in {startup_seconds:.3f}s.")


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _executable(root: Path) -> Path:
    if sys.platform == "darwin":
        return (
            root / "dist" / "PlateReaderDatabase.app" / "Contents" / "MacOS" / "PlateReaderDatabase"
        )
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return root / "dist" / "PlateReaderDatabase" / f"PlateReaderDatabase{suffix}"


def _wait_for_health(server: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 60
    url = f"http://127.0.0.1:{port}/_stcore/health"
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"Packaged server exited with status {server.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.read() == b"ok":
                    return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("Packaged Streamlit server did not become healthy")


if __name__ == "__main__":
    main()
