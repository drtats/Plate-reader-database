"""Count SQL calls for synthetic 96-well cultivation workflows in a temporary database."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import TypeVar

from plate_reader.application.contracts import Actor, ImportGrowthRun, Role, SearchRuns, UserId
from plate_reader.application.services.growth_cultivation_registry import (
    PreviewGrowthCultivationRegistryService,
    SaveGrowthCultivationRegistryService,
)
from plate_reader.application.services.growth_import import ImportGrowthRunService
from plate_reader.application.services.growth_workflow import SearchGrowthRunsService
from plate_reader.domain.growth.cultivation_registry import RegistrySettings
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    connect_database,
)
from plate_reader.infrastructure.database.dbapi import Connection, Cursor, SqlParameters

T = TypeVar("T")


class CountingConnection:
    """Count statements; no latency simulation or real laboratory database access."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.statements = 0

    @property
    def in_transaction(self) -> bool:
        return self.connection.in_transaction

    def execute(self, sql: str, parameters: SqlParameters = ()) -> Cursor:
        self.statements += 1
        return self.connection.execute(sql, parameters)

    def executemany(self, sql: str, parameters: Iterable[SqlParameters]) -> Cursor:
        rows = tuple(parameters)
        self.statements += len(rows)
        return self.connection.executemany(sql, rows)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def measure(root: Path) -> dict[str, dict[str, float | int]]:
    """Return local time and statement counts; remote elapsed time is not estimated."""

    actor = Actor(UserId("roundtrip-test"), "benchmark@example.invalid", Role.EDITOR)
    source = (root / "tests/fixtures/growth/with_time.csv").read_text()
    results: dict[str, dict[str, float | int]] = {}
    with tempfile.TemporaryDirectory(prefix="growth-roundtrips-") as directory:
        counted = CountingConnection(
            connect_database(
                DatabaseConfig(
                    Path(directory) / "synthetic.sqlite",
                    DatabaseBackend.FAKE_CLOUD,
                    root / "migrations",
                )
            )
        )
        repository = SqlPlateReaderRepository(counted)

        def timed(name: str, operation: Callable[[], T]) -> T:
            counted.statements = 0
            start = time.perf_counter()
            result = operation()
            results[name] = {
                "sql_statements": counted.statements,
                "local_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            return result

        try:
            run = ImportGrowthRunService(repository).execute(
                ImportGrowthRun(
                    actor,
                    "synthetic.csv",
                    hashlib.sha256(source.encode()).hexdigest(),
                    "growth-normalize/1.0.0",
                    "Synthetic benchmark",
                    "Synthetic benchmark",
                    date(2026, 9, 14),
                ),
                source,
            )
            changes = [
                {"position": f"{row}{column}", "strain": "MG1655", "medium": "M9"}
                for row in "ABCDEFGH"
                for column in range(1, 13)
            ]
            with repository.transaction():
                repository.update_well_layout(run.plate_id, changes)
            preview = timed(
                "preview",
                lambda: PreviewGrowthCultivationRegistryService(repository).execute(
                    actor, (run.plate_id,), RegistrySettings("ST", "MP96A")
                ),
            )
            timed(
                "save_96_well_ids",
                lambda: SaveGrowthCultivationRegistryService(repository).execute(actor, preview),
            )
            timed(
                "library_search",
                lambda: SearchGrowthRunsService(repository).execute(SearchRuns(actor)),
            )
        finally:
            counted.close()
    return results


if __name__ == "__main__":
    print(json.dumps(measure(Path(__file__).resolve().parents[1]), indent=2))
