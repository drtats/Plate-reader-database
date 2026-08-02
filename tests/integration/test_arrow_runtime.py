from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from plate_reader.arrow_runtime import configure_arrow_memory_pool

ROOT = Path(__file__).resolve().parents[2]


def test_arrow_runtime_selects_system_memory_pool() -> None:
    assert configure_arrow_memory_pool() == "system"


def test_real_plate_editor_survives_repeated_arrow_serialization() -> None:
    """Run out-of-process so a native allocator regression is reported as a test failure."""

    code = """
import os
import pyarrow as pa
from streamlit.testing.v1 import AppTest

os.environ["PLATE_READER_ENV"] = "development"
for _index in range(20):
    app = AppTest.from_file("tests/ui/plate_editor_app.py", default_timeout=30).run()
    assert not app.exception
assert pa.default_memory_pool().backend_name == "system"
"""
    environment = os.environ.copy()
    environment.pop("ARROW_DEFAULT_MEMORY_POOL", None)
    completed = subprocess.run(
        (sys.executable, "-c", code),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
