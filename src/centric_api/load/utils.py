from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .models import LoadProgressCallback


def _lookup_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _extract_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _json_dict(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}


def _emit_progress(
    progress_callback: LoadProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if progress_callback is not None:
        progress_callback(event)


def _run_id(job_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    safe_job = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in job_name)
    return f"{timestamp}-{safe_job}-{suffix}"


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
