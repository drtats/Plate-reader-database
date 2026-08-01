"""Bound in-memory text imports before parsing or hashing."""

from __future__ import annotations

MAX_GROWTH_SOURCE_BYTES = 25 * 1024 * 1024
MAX_MIC_SOURCE_BYTES = 5 * 1024 * 1024
MAX_PORTABLE_SOURCE_BYTES = 25 * 1024 * 1024


class SourceTooLargeError(ValueError):
    pass


def source_bytes_within_limit(text: str, *, max_bytes: int, kind: str) -> bytes:
    return binary_source_within_limit(text.encode("utf-8"), max_bytes=max_bytes, kind=kind)


def binary_source_within_limit(content: bytes, *, max_bytes: int, kind: str) -> bytes:
    if len(content) > max_bytes:
        raise SourceTooLargeError(
            f"{kind} source is {len(content):,} bytes; maximum accepted size is {max_bytes:,} bytes"
        )
    return content
