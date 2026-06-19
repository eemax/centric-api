from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..config import ConfigError
from .contracts import SnapshotDefinition, SnapshotOutput, SnapshotRecord

MANIFEST_NAME = "manifest.json"
SNAPSHOT_MANIFEST_VERSION = 1
_STREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def write_snapshot_artifacts(
    target_dir: Path,
    *,
    definition: SnapshotDefinition,
    output: SnapshotOutput,
    clean: bool = False,
) -> tuple[Path, dict[str, Any]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        _clean_target_dir(target_dir)
    else:
        _remove_previous_managed_files(target_dir)
    records_by_file = _records_by_file(output.records)
    files: list[dict[str, Any]] = []
    for relative_path, records in sorted(records_by_file.items()):
        path = target_dir / relative_path
        payload = _jsonl_payload(records)
        _replace_text(path, payload)
        files.append(
            {
                "path": relative_path.as_posix(),
                "records": len(records),
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
        )
    manifest = _manifest(definition=definition, output=output, files=files)
    manifest_payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _replace_text(target_dir / MANIFEST_NAME, manifest_payload)
    return target_dir / MANIFEST_NAME, manifest


def copy_snapshot_artifacts(
    source_dir: Path,
    target_dir: Path,
    *,
    clean: bool = False,
) -> tuple[Path, dict[str, Any]]:
    source_manifest_path = source_dir / MANIFEST_NAME
    if not source_manifest_path.is_file():
        raise ConfigError(f"Snapshot candidate manifest not found: {source_manifest_path}")
    try:
        manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid snapshot manifest: {source_manifest_path}") from exc

    target_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        _clean_target_dir(target_dir)
    else:
        _remove_previous_managed_files(target_dir)

    for item in manifest.get("files", []):
        relative = item.get("path") if isinstance(item, dict) else None
        if not isinstance(relative, str):
            continue
        source_path = _safe_child_path(source_dir, relative)
        target_path = _safe_child_path(target_dir, relative)
        if not source_path.is_file():
            raise ConfigError(f"Snapshot candidate file not found: {source_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
    shutil.copy2(source_manifest_path, target_dir / MANIFEST_NAME)
    return target_dir / MANIFEST_NAME, manifest


def _records_by_file(records: tuple[SnapshotRecord, ...]) -> dict[Path, list[SnapshotRecord]]:
    grouped: dict[Path, list[SnapshotRecord]] = defaultdict(list)
    for record in records:
        stream = stream_filename(record.stream)
        group_parts = [_safe_path_part(part) for part in record.group]
        grouped[Path(*group_parts, stream) if group_parts else Path(stream)].append(record)
    return dict(grouped)


def _jsonl_payload(records: list[SnapshotRecord]) -> str:
    lines: list[str] = []
    for record in sorted(records, key=lambda item: item.key):
        payload = _record_payload(record)
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines) + ("\n" if lines else "")


def _record_payload(record: SnapshotRecord) -> dict[str, Any]:
    if not isinstance(record.data, dict):
        raise ConfigError("Snapshot record data must be a dict.")
    key = str(record.key).strip()
    if not key:
        raise ConfigError(f"Snapshot record in stream {record.stream!r} has an empty key.")
    existing_key = record.data.get("_key")
    if existing_key is not None and str(existing_key) != key:
        raise ConfigError(f"Snapshot record {record.stream}:{key} has conflicting data['_key'].")
    return {"_key": key, **record.data}


def _manifest(
    *,
    definition: SnapshotDefinition,
    output: SnapshotOutput,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    groups = sorted({"/".join(record.group) for record in output.records})
    streams = sorted({stream_filename(record.stream) for record in output.records})
    return {
        "manifest_version": SNAPSHOT_MANIFEST_VERSION,
        "snapshot": definition.name,
        "title": definition.title,
        "description": definition.description,
        "version": definition.version,
        "group_levels": list(definition.group_levels),
        "record_count": len(output.records),
        "group_count": len(groups),
        "stream_count": len(streams),
        "file_count": len(files),
        "streams": streams,
        "groups": groups,
        "metrics": output.metrics or {},
        "files": files,
    }


def _replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def _clean_target_dir(target_dir: Path) -> None:
    for child in target_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _remove_previous_managed_files(target_dir: Path) -> None:
    manifest_path = target_dir / MANIFEST_NAME
    if not manifest_path.exists():
        unmanaged = [child for child in target_dir.iterdir() if not child.name.startswith(".")]
        if unmanaged:
            raise ConfigError(
                f"Snapshot directory is not empty and has no {MANIFEST_NAME}: {target_dir}. "
                "Use --clean to replace non-hidden contents."
            )
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid snapshot manifest: {manifest_path}") from exc
    for item in manifest.get("files", []):
        relative = item.get("path") if isinstance(item, dict) else None
        if not isinstance(relative, str):
            continue
        path = _safe_child_path(target_dir, relative)
        if path.is_file():
            path.unlink()
            _prune_empty_parents(path.parent, stop=target_dir)
    if manifest_path.is_file():
        manifest_path.unlink()


def _prune_empty_parents(path: Path, *, stop: Path) -> None:
    stop = stop.resolve()
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _safe_child_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    resolved_root = root.resolve()
    if path == resolved_root or resolved_root not in path.parents:
        raise ConfigError(f"Snapshot manifest path escapes output directory: {relative}")
    return path


def stream_filename(stream: str) -> str:
    text = stream.strip()
    if text.endswith(".jsonl"):
        text = text[: -len(".jsonl")]
    if text in {".", ".."}:
        raise ConfigError(f"Snapshot stream {stream!r} is not a valid filename.")
    if not _STREAM_RE.fullmatch(text):
        raise ConfigError(
            f"Snapshot stream {stream!r} must contain only letters, digits, dot, "
            "dash, or underscore."
        )
    return f"{text}.jsonl"


def _safe_path_part(value: str) -> str:
    text = str(value).strip()
    if not text:
        return "Unknown"
    text = text.replace("/", " - ").replace("\\", " - ")
    text = re.sub(r"\s+", " ", text)
    text = text.lstrip(".").strip()
    if not text or text in {".", ".."}:
        return "Unknown"
    return text.strip() or "Unknown"
