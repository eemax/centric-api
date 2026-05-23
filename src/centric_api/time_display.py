from __future__ import annotations

from datetime import UTC, datetime


def format_time_ago(value: object, *, now: datetime | None = None) -> str:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return "none"
    current = (now or datetime.now(UTC)).astimezone(UTC)
    seconds = int((current - timestamp).total_seconds())
    suffix = "ago"
    if seconds < 0:
        seconds = abs(seconds)
        suffix = "from now"
    if seconds < 60:
        return f"{seconds}s {suffix}"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {suffix}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {suffix}"
    days = hours // 24
    if days < 30:
        return f"{days}d {suffix}"
    months = days // 30
    if months < 12:
        return f"{months}mo {suffix}"
    years = max(1, days // 365)
    return f"{years}y {suffix}"


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
