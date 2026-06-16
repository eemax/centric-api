from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RAW_ACTIVE_DIR = "active"
RAW_COMPLETED_DIR = "runs"
RAW_FAILED_DIR = "failed"
RUNNING_MARKER = ".running.json"
COMPLETED_MARKER = ".completed.json"
FAILED_MARKER = ".failed.json"


def active_run_dir(raw_root: Path, run_id: str) -> Path:
    return raw_root / RAW_ACTIVE_DIR / run_id


def completed_run_dir(raw_root: Path, run_id: str) -> Path:
    return raw_root / RAW_COMPLETED_DIR / run_id


def failed_run_dir(raw_root: Path, run_id: str) -> Path:
    return raw_root / RAW_FAILED_DIR / run_id


def promote_run_dir(source: Path, target: Path) -> Path:
    if source == target:
        return target
    if target.exists():
        raise FileExistsError(f"Raw run target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        source.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    return target


def write_running_marker(
    run_dir: Path,
    *,
    run_id: str,
    mode: str,
    started_at: str,
) -> Path:
    return _write_marker(
        run_dir / RUNNING_MARKER,
        {
            "status": "running",
            "run_id": run_id,
            "mode": mode,
            "started_at": started_at,
        },
    )


def write_completed_marker(
    run_dir: Path,
    *,
    run_id: str,
    mode: str,
    started_at: str,
    completed_at: str,
    manifest_path: Path | None,
) -> Path:
    _remove_markers(run_dir)
    return _write_marker(
        run_dir / COMPLETED_MARKER,
        {
            "status": "completed",
            "run_id": run_id,
            "mode": mode,
            "started_at": started_at,
            "completed_at": completed_at,
            "manifest_path": str(manifest_path) if manifest_path is not None else None,
        },
    )


def write_failed_marker(
    run_dir: Path,
    *,
    run_id: str,
    mode: str,
    started_at: str,
    failed_at: str,
    manifest_path: Path | None,
    reason: str,
    failures: list[dict[str, str]],
) -> Path:
    _remove_markers(run_dir)
    return _write_marker(
        run_dir / FAILED_MARKER,
        {
            "status": "failed",
            "run_id": run_id,
            "mode": mode,
            "started_at": started_at,
            "failed_at": failed_at,
            "manifest_path": str(manifest_path) if manifest_path is not None else None,
            "reason": reason,
            "failures": failures,
        },
    )


def raw_run_lifecycle(run_dir: Path) -> str:
    if (run_dir / COMPLETED_MARKER).is_file():
        return "completed"
    if (run_dir / FAILED_MARKER).is_file():
        return "failed"
    if (run_dir / RUNNING_MARKER).is_file():
        return "running"
    return "unknown"


def _remove_markers(run_dir: Path) -> None:
    for marker in (RUNNING_MARKER, COMPLETED_MARKER, FAILED_MARKER):
        (run_dir / marker).unlink(missing_ok=True)


def _write_marker(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "COMPLETED_MARKER",
    "FAILED_MARKER",
    "RAW_ACTIVE_DIR",
    "RAW_COMPLETED_DIR",
    "RAW_FAILED_DIR",
    "RUNNING_MARKER",
    "active_run_dir",
    "completed_run_dir",
    "failed_run_dir",
    "promote_run_dir",
    "raw_run_lifecycle",
    "write_completed_marker",
    "write_failed_marker",
    "write_running_marker",
]
