from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawFile:
    path: Path
    endpoint: str
    is_delta: bool
    source_run_id: str
    run_mode: str | None = None
    manifest_path: Path | None = None
    manifest_sha256: str | None = None


def discover_raw_files(raw_dir: Path) -> list[RawFile]:
    if not raw_dir.exists():
        return []
    files: list[RawFile] = []
    search_roots = [raw_dir] if (raw_dir / "manifest.json").is_file() else [raw_dir / "runs"]
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        for path in search_root.rglob("*.jsonl"):
            _append_raw_file(files, raw_dir=raw_dir, path=path)
    return sorted(files, key=lambda item: (_run_sort_key(item), item.endpoint, str(item.path)))


def _append_raw_file(files: list[RawFile], *, raw_dir: Path, path: Path) -> None:
    if path.name.startswith("."):
        return
    endpoint, is_delta = _endpoint_from_filename(path.name)
    if endpoint is None:
        return
    manifest = _load_manifest(path.parent)
    manifest_endpoint = _manifest_endpoint_for_file(manifest, path.name)
    if _manifest_has_endpoint_records(manifest) and manifest_endpoint is None:
        return
    if manifest_endpoint is not None:
        endpoint, endpoint_manifest = manifest_endpoint
    else:
        endpoint_manifest = None
    source_run_id = _manifest_run_id(manifest) or path.parent.name
    run_mode = _manifest_mode(manifest)
    manifest_path = path.parent / "manifest.json" if manifest is not None else None
    files.append(
        RawFile(
            path=path,
            endpoint=endpoint,
            is_delta=_manifest_file_is_delta(endpoint_manifest, default=is_delta),
            source_run_id=source_run_id,
            run_mode=run_mode,
            manifest_path=manifest_path,
            manifest_sha256=_sha256(manifest_path) if manifest_path else None,
        )
    )


def _endpoint_from_filename(filename: str) -> tuple[str | None, bool]:
    if filename.endswith(".delta.jsonl"):
        return filename[: -len(".delta.jsonl")], True
    if filename.endswith(".jsonl"):
        return filename[: -len(".jsonl")], False
    return None, False


def _load_manifest(directory: Path) -> dict[str, Any] | None:
    path = directory / "manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _manifest_run_id(manifest: dict[str, Any] | None) -> str | None:
    value = manifest.get("run_id") if manifest else None
    return str(value) if value else None


def _manifest_mode(manifest: dict[str, Any] | None) -> str | None:
    value = manifest.get("mode") if manifest else None
    return str(value) if value else None


def _manifest_endpoint_for_file(
    manifest: dict[str, Any] | None,
    filename: str,
) -> tuple[str, dict[str, Any]] | None:
    if manifest is None:
        return None
    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, dict):
        return None
    for endpoint_name, endpoint in endpoints.items():
        if not isinstance(endpoint, dict) or endpoint.get("file") != filename:
            continue
        return str(endpoint_name), endpoint
    return None


def _manifest_has_endpoint_records(manifest: dict[str, Any] | None) -> bool:
    endpoints = manifest.get("endpoints") if manifest else None
    return isinstance(endpoints, dict) and bool(endpoints)


def _manifest_file_is_delta(
    endpoint_manifest: dict[str, Any] | None,
    *,
    default: bool,
) -> bool:
    if endpoint_manifest is None:
        return default
    is_delta = endpoint_manifest.get("is_delta")
    return bool(is_delta) if isinstance(is_delta, bool) else default


def _run_sort_key(raw_file: RawFile) -> tuple[int, str]:
    if raw_file.manifest_path and raw_file.manifest_path.is_file():
        manifest = _load_manifest(raw_file.manifest_path.parent)
        started_at = manifest.get("started_at") if manifest else None
        if isinstance(started_at, str):
            return (0, started_at)
    return (1, str(raw_file.path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
