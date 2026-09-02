"""Opt-in live contract check for an isolated, disposable Turso database."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import pytest

from plate_reader.application.contracts import Actor, ImportGrowthRun, Role, UserId
from plate_reader.application.ports import PlateReaderRepository
from plate_reader.application.services import ImportGrowthRunService
from plate_reader.domain.growth import GROWTH_NORMALIZATION_VERSION
from plate_reader.infrastructure.database import (
    SqlPlateReaderRepository,
    TursoDatabaseConfig,
    backup_complete_database,
    connect_turso_database,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"


@pytest.mark.remote
def test_live_turso_repository_import_transaction_and_backup_contract(tmp_path: Path) -> None:
    if os.getenv("PLATE_READER_RUN_REMOTE_TESTS") != "1":
        pytest.skip("Set PLATE_READER_RUN_REMOTE_TESTS=1 for the opt-in live Turso contract")
    database_url = os.environ["TURSO_TEST_DATABASE_URL"]
    auth_token = os.environ["TURSO_TEST_AUTH_TOKEN"]
    connection = connect_turso_database(TursoDatabaseConfig(database_url, auth_token, MIGRATIONS))
    repository = SqlPlateReaderRepository(connection)
    try:
        assert isinstance(repository, PlateReaderRepository)
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (3,)
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            pytest.fail("The live contract requires a fresh isolated Turso test database")

        actor = Actor(UserId("remote-contract-editor"), "remote@example.invalid", Role.EDITOR)
        with repository.transaction():
            repository.upsert_user(
                {
                    "user_id": actor.user_id,
                    "email": actor.email,
                    "display_name": "Remote Contract Editor",
                    "role": actor.role,
                    "is_active": True,
                }
            )
        csv_text = (ROOT / "tests" / "fixtures" / "growth" / "with_time.csv").read_text(
            encoding="utf-8"
        )
        command = ImportGrowthRun(
            actor=actor,
            source_name="remote-contract.csv",
            source_sha256=hashlib.sha256(csv_text.encode()).hexdigest(),
            parser_version=GROWTH_NORMALIZATION_VERSION,
            experiment_name="Remote contract",
            plate_name="Remote contract plate",
            experiment_date=date(2026, 8, 1),
            idempotency_key="remote-contract-growth-v1",
        )
        service = ImportGrowthRunService(repository)
        first = service.execute(command, csv_text)
        second = service.execute(command, csv_text)
        assert first.created is True
        assert second.created is False
        assert first.measurement_count == 384
        assert repository.load_plate(first.plate_id) is not None

        with pytest.raises(RuntimeError, match="rollback proof"), repository.transaction():
            connection.execute(
                "INSERT INTO saved_options(option_type, value, created_by, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("test", "must-rollback", actor.user_id, "2026-08-01T00:00:00+00:00"),
            )
            raise RuntimeError("rollback proof")
        assert (
            connection.execute("SELECT 1 FROM saved_options WHERE option_type = 'test'").fetchone()
            is None
        )

        backup = tmp_path / "live-turso-contract.sqlite"
        backup_complete_database(connection, backup, MIGRATIONS)
        assert backup.is_file()
    finally:
        connection.close()
