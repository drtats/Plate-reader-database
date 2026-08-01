import pytest

from plate_reader.infrastructure.database.repository import (
    InvalidRepositoryValueError,
    _field_filter_sequence,
    _filter_string_sequence,
    _json_object_items,
    _normalized_mic_group_value,
)


def test_mic_search_filter_sequences_reject_malformed_values() -> None:
    with pytest.raises(InvalidRepositoryValueError, match="filter strings"):
        _filter_string_sequence("not-a-sequence-of-values")
    with pytest.raises(InvalidRepositoryValueError, match="MIC field filters"):
        _field_filter_sequence("not-a-sequence-of-pairs")
    with pytest.raises(InvalidRepositoryValueError, match="field and value"):
        _field_filter_sequence((("only-one-value",),))


def test_mic_custom_json_and_group_normalization_are_defensive() -> None:
    assert _json_object_items(None) == ()
    assert _json_object_items("not-json") == ()
    assert _json_object_items("[]") == ()
    assert _json_object_items('{"":"ignored","host":"human"}') == (("host", "human"),)
    assert _normalized_mic_group_value(None) == "Unknown"
    assert _normalized_mic_group_value("  ") == "Unknown"
    assert _normalized_mic_group_value(" strain ") == "strain"
