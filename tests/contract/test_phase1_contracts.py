from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from plate_reader.application.contracts import (
    ALGORITHM_VERSIONS,
    AssayType,
    ExperimentId,
    PlateId,
    Role,
)
from plate_reader.application.ports import PlateReaderRepository
from plate_reader.application.ports.repositories import (
    ConcentrationRange,
    PlateSnapshot,
    RunSummary,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def test_algorithm_identifiers_are_frozen() -> None:
    assert ALGORITHM_VERSIONS == {
        "growth_normalization": "growth-normalize/1.0.0",
        "growth_background": "growth-background/1.0.0",
        "mic_endpoint": "mic-endpoint/1.0.0",
    }
    assert set(AssayType) == {AssayType.GROWTH, AssayType.MIC, AssayType.MIXED}
    assert set(Role) == {Role.VIEWER, Role.EDITOR, Role.ADMIN}


def test_repository_result_contracts_are_storage_neutral() -> None:
    summary = RunSummary(
        experiment_id=ExperimentId("experiment-1"),
        plate_id=PlateId("plate-1"),
        experiment_name="Synthetic",
        plate_name="Plate 1",
        assay_type=AssayType.GROWTH,
        experiment_date="2026-01-01",
        project=None,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    snapshot = PlateSnapshot(
        plate_id=PlateId("plate-1"),
        metadata={},
        wells=(),
        raw_observations=(),
        revisions=(),
    )
    assert summary.assay_type is AssayType.GROWTH
    assert summary.strains == ()
    assert summary.treatments == ()
    assert summary.concentration_ranges == ()
    assert ConcentrationRange(0.25, 1.0, "ug/mL") == ConcentrationRange(0.25, 1.0, "ug/mL")
    assert snapshot.plate_id == "plate-1"
    assert PlateReaderRepository.__name__ == "PlateReaderRepository"


def test_growth_golden_fixture_shapes() -> None:
    with_time = read_json("golden/growth_normalized_with_time.json")
    without_time = read_json("golden/growth_normalized_without_time.json")
    backgrounds = read_json("golden/growth_backgrounds.json")
    edges = read_json("golden/growth_background_edge_cases.json")
    assert len(with_time) == 384
    assert len(without_time) == 384
    assert sorted({row["time_min"] for row in with_time}) == [0.0, 10.0, 20.0, 30.0]
    assert sorted({row["time_min"] for row in without_time}) == [5.0, 15.0, 25.0, 35.0]
    assert len(backgrounds) == 8
    assert edges["groups_emitted"] == ["high_cv", "valid"]
    assert edges["missing_group_emitted"] is False
    assert edges["high_cv_minimum"] > 0.1


def test_mic_golden_fixture_covers_edge_cases() -> None:
    golden = read_json("golden/mic_endpoint.json")
    assert golden["background_value"] == 0.05
    results = {row["strain"]: row for row in golden["results"]}
    assert (results["strain_normal"]["mic_operator"], results["strain_normal"]["mic_value"]) == (
        "=",
        2.0,
    )
    assert results["strain_all_growth"]["mic_operator"] == ">"
    assert results["strain_all_no_growth"]["mic_operator"] == "<="
    assert "bounce" in results["strain_bounce"]["warning"].lower()


def test_legacy_databases_are_synthetic_and_well_formed() -> None:
    growth = sqlite3.connect(FIXTURES / "legacy" / "growth_v4.sqlite")
    mic = sqlite3.connect(FIXTURES / "legacy" / "mic_legacy.sqlite")
    try:
        assert growth.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert growth.execute("SELECT count(*) FROM measurements").fetchone() == (384,)
        assert mic.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert mic.execute("SELECT count(*) FROM wells").fetchone() == (96,)
        assert mic.execute("SELECT count(*) FROM mic_results").fetchone() == (4,)
        text = " ".join(
            str(value)
            for connection in (growth, mic)
            for row in connection.execute("SELECT name FROM sqlite_master")
            for value in row
        )
        assert "tatsuya" not in text.lower()
    finally:
        growth.close()
        mic.close()


def test_phase1_contract_hashes_match_freeze_manifest() -> None:
    manifests = sorted((ROOT / "docs" / "contracts").glob("phase*-freeze.json"))
    assert [manifest.name for manifest in manifests] == [
        "phase1-freeze.json",
        "phase2-freeze.json",
    ]
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["freeze_version"] == 1
        for relative_path, expected_hash in manifest["sha256"].items():
            content = (ROOT / relative_path).read_bytes()
            assert hashlib.sha256(content).hexdigest() == expected_hash, relative_path


def read_json(relative_path: str) -> object:
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))
