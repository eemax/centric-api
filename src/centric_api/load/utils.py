from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..artifact_names import allocate_artifact_dir
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


def _allocate_run_dir(root: Path, job_name: str, started_at: str) -> tuple[str, Path]:
    return allocate_artifact_dir(root, job_name, started_at)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
