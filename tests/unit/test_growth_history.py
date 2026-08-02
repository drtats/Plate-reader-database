from __future__ import annotations

from plate_reader.ui.growth_history import (
    growth_activity_items,
    growth_background_history_items,
)


def test_background_history_is_friendly_and_preserves_every_technical_field() -> None:
    previous = {
        "revision_id": "revision-1",
        "algorithm_name": "growth_background",
        "algorithm_version": "1.0",
        "is_current": 0,
        "created_by": "user-1",
        "created_at": "2026-08-01T10:00:00Z",
        "input_sha256": "old-hash",
        "parameters_json": '{"method":"mean"}',
    }
    current = {
        "revision_id": "revision-2",
        "algorithm_name": "growth_background",
        "algorithm_version": "1.1",
        "is_current": 1,
        "created_by": "user-2",
        "created_at": "2026-08-01T11:00:00Z",
        "input_sha256": "new-hash",
        "parameters_json": "{}",
    }

    items = growth_background_history_items(
        (previous, current),
        current_is_stale=True,
    )

    assert [item.status for item in items] == [
        "Current · stale — recompute required",
        "Previous calculation",
    ]
    assert items[0].calculated_by == "user-2"
    assert items[0].method == "Time-course background · 1.1"
    assert dict(items[0].details) == current
    assert dict(items[1].details) == previous


def test_activity_log_has_friendly_actions_and_lossless_details() -> None:
    imported = {
        "event_id": "event-1",
        "event_type": "growth_imported",
        "actor_id": "user-1",
        "occurred_at": "2026-08-01T10:00:00Z",
        "details_json": '{"raw_rows":13920}',
    }
    custom = {
        "event_id": "event-2",
        "event_type": "growth_reviewed_manually",
        "actor_id": "user-2",
        "occurred_at": "2026-08-01T11:00:00Z",
        "details_json": "{}",
    }

    items = growth_activity_items((imported, custom))

    assert [item.action for item in items] == [
        "Growth reviewed manually",
        "Growth run imported",
    ]
    assert items[0].user == "user-2"
    assert items[0].timestamp == "2026-08-01T11:00:00Z"
    assert dict(items[0].details) == custom
    assert dict(items[1].details) == imported
