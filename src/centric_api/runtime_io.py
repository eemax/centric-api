from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

UtcIsoFn = Callable[[], str]


def try_acquire_lock(path: Path, name: str, *, utc_iso: UtcIsoFn) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return f"{name} lock exists: {path}"
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": os.getpid(), "created_at": utc_iso()}) + "\n")
    return None


def release_lock(path: Path) -> None:
    path.unlink(missing_ok=True)


def append_cron_event(
    path: Path,
    *,
    record_type: str,
    utc_iso: UtcIsoFn,
    **payload: Any,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": utc_iso(),
                    "record_type": record_type,
                    **payload,
                },
                default=str,
            )
            + "\n"
        )


def append_cron_fetch_records(
    path: Path,
    *,
    records: list[dict[str, Any]],
    stderr: str,
    exit_code: int,
    duration_seconds: float,
    utc_iso: UtcIsoFn,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps({"timestamp": utc_iso(), **record}, default=str) + "\n")
        if stderr.strip():
            fh.write(
                json.dumps(
                    {
                        "timestamp": utc_iso(),
                        "record_type": "fetch_stderr",
                        "stderr": stderr.strip(),
                    },
                    default=str,
                )
                + "\n"
            )
        fh.write(
            json.dumps(
                {
                    "timestamp": utc_iso(),
                    "record_type": "cron_fetch_summary",
                    "exit_code": exit_code,
                    "duration_seconds": round(duration_seconds, 3),
                },
                default=str,
            )
            + "\n"
        )


def parse_jsonl(value: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in value.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            records.append({"record_type": "fetch_stdout", "line": text})
            continue
        if isinstance(payload, dict):
            records.append(payload)
        else:
            records.append({"record_type": "fetch_stdout", "value": payload})
    return records


def safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0
