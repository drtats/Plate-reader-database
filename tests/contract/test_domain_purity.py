from __future__ import annotations

import ast
from pathlib import Path

from plate_reader.application.contracts import ALGORITHM_VERSIONS
from plate_reader.domain.growth import GROWTH_BACKGROUND_VERSION, GROWTH_NORMALIZATION_VERSION
from plate_reader.domain.mic import MIC_ENDPOINT_VERSION

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "src" / "plate_reader" / "domain"
FORBIDDEN_IMPORTS = {
    "libsql",
    "os",
    "pathlib",
    "pyturso",
    "sqlite3",
    "streamlit",
    "turso_serverless",
}


def test_domain_has_no_ui_database_secret_or_filesystem_imports() -> None:
    violations: list[str] = []
    for path in sorted(DOMAIN.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = (node.module.split(".", maxsplit=1)[0],)
            for module in imported:
                if module in FORBIDDEN_IMPORTS:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {module}")
    assert violations == []


def test_domain_algorithm_versions_match_frozen_application_contract() -> None:
    assert ALGORITHM_VERSIONS == {
        "growth_normalization": GROWTH_NORMALIZATION_VERSION,
        "growth_background": GROWTH_BACKGROUND_VERSION,
        "mic_endpoint": MIC_ENDPOINT_VERSION,
    }
