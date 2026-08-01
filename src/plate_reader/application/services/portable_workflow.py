"""Authorization, limits, and hash checks for portable database import."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Protocol

from plate_reader.application.contracts import Actor, ImportPortableRun, Role
from plate_reader.application.ports.portable import (
    PortableImportPreviewData,
    PortableImportResultData,
    PortableRunImporter,
)
from plate_reader.application.services.authorization import require_role
from plate_reader.application.services.growth_import import SourceHashMismatchError
from plate_reader.application.services.source_limits import (
    MAX_PORTABLE_SOURCE_BYTES,
    binary_source_within_limit,
)


class PortableAuthorizationRepository(Protocol):
    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...


class PreviewPortableRunService:
    def __init__(
        self, repository: PortableAuthorizationRepository, importer: PortableRunImporter
    ) -> None:
        self.repository = repository
        self.importer = importer

    def execute(self, actor: Actor, content: bytes) -> PortableImportPreviewData:
        require_role(self.repository, actor, {Role.VIEWER, Role.EDITOR, Role.ADMIN})
        return self.importer.preview(
            binary_source_within_limit(
                content,
                max_bytes=MAX_PORTABLE_SOURCE_BYTES,
                kind="Portable SQLite",
            )
        )


class ImportPortableRunService:
    def __init__(
        self, repository: PortableAuthorizationRepository, importer: PortableRunImporter
    ) -> None:
        self.repository = repository
        self.importer = importer

    def execute(
        self, command: ImportPortableRun, content: bytes
    ) -> PortableImportPreviewData | PortableImportResultData:
        actor_id = require_role(self.repository, command.actor, {Role.EDITOR, Role.ADMIN})
        validated = binary_source_within_limit(
            content,
            max_bytes=MAX_PORTABLE_SOURCE_BYTES,
            kind="Portable SQLite",
        )
        actual_hash = hashlib.sha256(validated).hexdigest()
        if actual_hash != command.archive_sha256.casefold():
            raise SourceHashMismatchError(
                f"Portable source hash mismatch: expected {command.archive_sha256}, "
                f"got {actual_hash}"
            )
        if command.dry_run:
            return self.importer.preview(validated)
        return self.importer.import_content(
            validated,
            actor_id=actor_id,
            collision_policy=command.collision_policy,
        )
