from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError
from .defaults import DEFAULT_OVERLAP_DAYS, DEFAULT_OVERLAP_MINUTES


def derive_delta_floor(
    delta_state: dict[str, Any],
    endpoint_name: str,
    overlap_minutes: int,
    overlap_days: int,
    *,
    utc_iso,
) -> str | None:
    endpoint_state = delta_state.get("endpoints", {}).get(endpoint_name, {})
    if not isinstance(endpoint_state, dict):
        return None
    started_at = parse_utc_iso(endpoint_state.get("last_successful_fetch_start"))
    if started_at is None:
        return None
    return utc_iso(started_at - timedelta(minutes=overlap_minutes, days=overlap_days))


def update_delta_state_for_endpoint(
    delta_state: dict[str, Any],
    *,
    endpoint_name: str,
    status: str,
    attempt_start: str,
    attempt_end: str,
    error: str | None,
) -> None:
    endpoints = delta_state.setdefault("endpoints", {})
    if not isinstance(endpoints, dict):
        endpoints = {}
        delta_state["endpoints"] = endpoints
    existing = endpoints.get(endpoint_name, {})
    if not isinstance(existing, dict):
        existing = {}
    existing["last_attempted_fetch_start"] = attempt_start
    existing["last_attempted_fetch_end"] = attempt_end
    existing["last_attempted_status"] = status
    existing["last_attempted_error"] = error
    if status == "OK":
        existing["last_successful_fetch_start"] = attempt_start
        existing["last_successful_fetch_end"] = attempt_end
    endpoints[endpoint_name] = existing
    delta_state["version"] = 1
    delta_state["updated_at"] = attempt_end


def load_delta_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "version": 1,
            "updated_at": None,
            "overlap_minutes": DEFAULT_OVERLAP_MINUTES,
            "overlap_days": DEFAULT_OVERLAP_DAYS,
            "endpoints": {},
        }
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Delta state root must be an object: {path}")
    endpoints = payload.get("endpoints", {})
    if not isinstance(endpoints, dict):
        raise ConfigError(f"Delta state endpoints must be an object: {path}")
    return {
        "version": 1,
        "updated_at": payload.get("updated_at"),
        "overlap_minutes": normalize_int(payload.get("overlap_minutes"), DEFAULT_OVERLAP_MINUTES),
        "overlap_days": normalize_int(payload.get("overlap_days"), DEFAULT_OVERLAP_DAYS),
        "endpoints": endpoints,
    }


def write_delta_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.tmp"
    temp_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    temp_path.replace(path)


def parse_utc_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default
