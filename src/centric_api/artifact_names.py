from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


def artifact_slug(value: str, *, default: str = "artifact") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or default


def artifact_timestamp(value: str | datetime | None = None) -> str:
    parsed = _artifact_datetime(value)
    return parsed.strftime("%Y-%m-%d-%H%M")


def artifact_base_name(name: str, value: str | datetime | None = None) -> str:
    return f"{artifact_slug(name)}-{artifact_timestamp(value)}"


def allocate_artifact_name(
    base_name: str,
    exists: Callable[[str], bool],
    *,
    limit: int = 10_000,
) -> str:
    for index in range(1, limit + 1):
        candidate = base_name if index == 1 else f"{base_name}-{index}"
        if not exists(candidate):
            return candidate
    raise RuntimeError(f"Could not allocate artifact name for {base_name}.")


def allocate_artifact_dir(
    root: Path,
    name: str,
    value: str | datetime | None = None,
    *,
    limit: int = 10_000,
) -> tuple[str, Path]:
    base_name = artifact_base_name(name, value)
    for index in range(1, limit + 1):
        run_id = base_name if index == 1 else f"{base_name}-{index}"
        output_dir = root / run_id
        try:
            output_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return run_id, output_dir
    raise RuntimeError(f"Could not allocate artifact directory for {base_name}.")


def allocate_artifact_path(
    root: Path,
    name: str,
    value: str | datetime | None = None,
    *,
    extension: str,
    limit: int = 10_000,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    suffix = extension if extension.startswith(".") else f".{extension}"
    base_name = artifact_base_name(name, value)
    artifact_name = allocate_artifact_name(
        base_name,
        lambda candidate: (root / f"{candidate}{suffix}").exists(),
        limit=limit,
    )
    return root / f"{artifact_name}{suffix}"


def _artifact_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC)
        return value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
        return parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
