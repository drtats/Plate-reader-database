"""Storage-neutral portable import records and adapter port."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PortableImportPreviewData:
    export_id: str
    file_sha256: str
    plate_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    table_counts: Mapping[str, int]
    collisions: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PortableImportResultData:
    export_id: str
    file_sha256: str
    created: bool
    table_counts: Mapping[str, int]
    collisions: Mapping[str, int]
    plate_id_map: Mapping[str, str]
    revision_id_map: Mapping[str, str]


class PortableRunImporter(Protocol):
    def preview(self, content: bytes) -> PortableImportPreviewData: ...

    def import_content(
        self, content: bytes, *, actor_id: str, collision_policy: str
    ) -> PortableImportResultData: ...
