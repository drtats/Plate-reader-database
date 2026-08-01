"""Version smoke tests."""

from plate_reader import __version__


def test_version_is_semantic() -> None:
    parts = __version__.split(".")

    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
