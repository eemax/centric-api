from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime_io import (
    append_cron_event,
    append_cron_fetch_records,
    release_lock,
    try_acquire_lock,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime | None = None) -> str:
    return (
        (value or utc_now())
        .astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def try_acquire_fetch_lock(path: Path) -> str | None:
    return try_acquire_lock(path, "fetch", utc_iso=utc_iso)


def try_acquire_download_lock(path: Path) -> str | None:
    return try_acquire_lock(path, "download", utc_iso=utc_iso)


def try_acquire_bundle_lock(path: Path) -> str | None:
    return try_acquire_lock(path, "bundle", utc_iso=utc_iso)


def release_fetch_lock(path: Path) -> None:
    release_lock(path)


def release_download_lock(path: Path) -> None:
    release_lock(path)


def release_bundle_lock(path: Path) -> None:
    release_lock(path)


def append_cron_log_event(path: Path, *, record_type: str, **payload: Any) -> None:
    append_cron_event(path, record_type=record_type, utc_iso=utc_iso, **payload)


def append_cron_log_fetch_records(
    path: Path,
    *,
    records: list[dict[str, Any]],
    stderr: str,
    exit_code: int,
    duration_seconds: float,
) -> None:
    append_cron_fetch_records(
        path,
        records=records,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        utc_iso=utc_iso,
    )
