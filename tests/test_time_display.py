from __future__ import annotations

from datetime import UTC, datetime

from centric_api.time_display import format_time_ago


def test_format_time_ago_uses_compact_units() -> None:
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)

    assert format_time_ago("2026-05-23T11:59:30Z", now=now) == "30s ago"
    assert format_time_ago("2026-05-23T11:45:00Z", now=now) == "15m ago"
    assert format_time_ago("2026-05-23T09:00:00Z", now=now) == "3h ago"
    assert format_time_ago("2026-05-20T12:00:00Z", now=now) == "3d ago"
    assert format_time_ago("2026-03-23T12:00:00Z", now=now) == "2mo ago"


def test_format_time_ago_handles_empty_and_future_values() -> None:
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)

    assert format_time_ago(None, now=now) == "none"
    assert format_time_ago("not-a-date", now=now) == "none"
    assert format_time_ago("2026-05-23T12:05:00Z", now=now) == "5m from now"
