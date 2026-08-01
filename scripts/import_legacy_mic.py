"""Preview or import a batch of legacy MIC SQLite databases."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.infrastructure.database import (
    DatabaseBackend,
    DatabaseConfig,
    SqlPlateReaderRepository,
    apply_migrations,
    connect_database,
)
from plate_reader.infrastructure.importers import import_legacy_mic_file

DEFAULT_ACTOR_ID = "legacy-mic-migration-editor"
DEFAULT_ACTOR_EMAIL = "legacy-mic-migration@example.invalid"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sources = discover_sources(args.sources)
    if not sources:
        parser.error("No legacy MIC SQLite files were found")
    if args.bootstrap_editor and not args.commit:
        parser.error("--bootstrap-editor is a write and therefore requires --commit")
    root = Path(__file__).resolve().parents[1]
    if args.commit or args.database.is_file():
        connection = connect_database(
            DatabaseConfig(args.database, args.backend, root / "migrations"),
            migrate=args.commit,
        )
    else:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        apply_migrations(connection, root / "migrations")
    repository = SqlPlateReaderRepository(connection)
    actor = Actor(UserId(args.actor_id), args.actor_email, Role.EDITOR)
    try:
        if args.bootstrap_editor:
            bootstrap_editor(repository, actor)
        files = []
        for source in sources:
            try:
                report = import_legacy_mic_file(
                    source,
                    repository,
                    actor,
                    dry_run=not args.commit,
                    allow_derived_differences=args.allow_derived_differences,
                )
                files.append({"source": str(source), "ok": True, "report": asdict(report)})
            except (OSError, sqlite3.Error, ValueError, PermissionError) as error:
                files.append(
                    {
                        "source": str(source),
                        "ok": False,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
    finally:
        connection.close()
    payload = {
        "mode": "commit" if args.commit else "dry-run",
        "backend": args.backend.value,
        "database": str(args.database),
        "allow_derived_differences": args.allow_derived_differences,
        "files": files,
    }
    rendered = json.dumps(_json_ready(payload), indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if any(not item["ok"] for item in files):
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Destination database path")
    parser.add_argument("sources", nargs="+", type=Path, help="Legacy files or directories")
    parser.add_argument(
        "--backend",
        choices=tuple(DatabaseBackend),
        default=DatabaseBackend.FAKE_CLOUD,
        type=DatabaseBackend,
    )
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--actor-id", default=DEFAULT_ACTOR_ID)
    parser.add_argument("--actor-email", default=DEFAULT_ACTOR_EMAIL)
    parser.add_argument("--bootstrap-editor", action="store_true")
    parser.add_argument(
        "--allow-derived-differences",
        action="store_true",
        help="Import immutable raw data and recompute results after reviewing listed differences",
    )
    parser.add_argument("--report", type=Path)
    return parser


def discover_sources(inputs: list[Path]) -> list[Path]:
    discovered: set[Path] = set()
    for value in inputs:
        if value.is_dir():
            discovered.update(
                path.resolve()
                for path in value.iterdir()
                if path.is_file() and path.suffix.casefold() in {".sqlite", ".sqlite3", ".db"}
            )
        else:
            discovered.add(value.resolve())
    return sorted(discovered)


def bootstrap_editor(repository: SqlPlateReaderRepository, actor: Actor) -> None:
    existing = repository.user_by_email(actor.email)
    if existing is not None:
        if str(existing["user_id"]) != actor.user_id:
            raise PermissionError("Existing migration email belongs to a different user ID")
        if not bool(existing["is_active"]):
            raise PermissionError("Existing migration user is inactive")
        if Role(str(existing["role"])) not in {Role.EDITOR, Role.ADMIN}:
            raise PermissionError(
                "Existing migration user is not an editor/admin; bootstrap will not elevate it"
            )
        return
    with repository.transaction():
        repository.upsert_user(
            {
                "user_id": actor.user_id,
                "email": actor.email,
                "display_name": "Legacy MIC Migration Editor",
                "role": actor.role,
                "is_active": True,
            }
        )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    return value


if __name__ == "__main__":
    main()
