from __future__ import annotations

import pytest

from plate_reader.application.services.source_limits import (
    SourceTooLargeError,
    binary_source_within_limit,
    source_bytes_within_limit,
)


def test_source_limit_counts_encoded_bytes() -> None:
    assert source_bytes_within_limit("é", max_bytes=2, kind="Fixture") == b"\xc3\xa9"
    with pytest.raises(SourceTooLargeError, match="3 bytes"):
        source_bytes_within_limit("abc", max_bytes=2, kind="Fixture")
    assert binary_source_within_limit(b"ab", max_bytes=2, kind="Fixture") == b"ab"
