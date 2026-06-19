from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ConfigError
from .artifacts import MANIFEST_NAME, stream_filename
from .contracts import SnapshotRecordIdentity


@dataclass(frozen=True)
class SnapshotArtifactRecord:
    identity: SnapshotRecordIdentity
    data: dict[str, Any]
    relative_path: Path


@dataclass(frozen=True)
class SnapshotArtifactSet:
    root: Path
    manifest: dict[str, Any]
    records: dict[SnapshotRecordIdentity, SnapshotArtifactRecord]


def read_snapshot_artifacts(root: Path) -> SnapshotArtifactSet:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ConfigError(f"Snapshot manifest not found: {manifest_path}")
    manifest = load_json_object(manifest_path)
    records: dict[SnapshotRecordIdentity, SnapshotArtifactRecord] = {}
    for item in manifest.get("files", []):
        relative = item.get("path") if isinstance(item, dict) else None
        if not isinstance(relative, str):
            continue
        relative_path = Path(relative)
        stream = _stream_from_relative_path(relative_path)
        group = tuple(relative_path.parts[:-1])
        path = _safe_child_path(root, relative)
        if not path.is_file():
            raise ConfigError(f"Snapshot data file not found: {path}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"Invalid JSONL in {path}:{line_number}") from exc
            if not isinstance(payload, dict):
                raise ConfigError(f"Snapshot record must be an object: {path}:{line_number}")
            key = str(payload.get("_key") or "").strip()
            if not key:
                raise ConfigError(f"Snapshot record missing _key: {path}:{line_number}")
            identity = SnapshotRecordIdentity(group=group, stream=stream, key=key)
            if identity in records:
                raise ConfigError(f"Duplicate snapshot record: {identity_label(identity)}")
            records[identity] = SnapshotArtifactRecord(
                identity=identity,
                data=payload,
                relative_path=relative_path,
            )
    return SnapshotArtifactSet(root=root, manifest=manifest, records=records)


def identity_label(identity: SnapshotRecordIdentity) -> str:
    group = "/".join(identity.group)
    prefix = f"{group}/" if group else ""
    return f"{prefix}{stream_filename(identity.stream)}:{identity.key}"


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON file: {path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"JSON file must contain an object: {path}")
    return payload


def _stream_from_relative_path(path: Path) -> str:
    name = path.name
    expected_suffix = ".jsonl"
    if not name.endswith(expected_suffix):
        raise ConfigError(f"Snapshot data file must end with .jsonl: {path}")
    return name[: -len(expected_suffix)]


def _safe_child_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    resolved_root = root.resolve()
    if path == resolved_root or resolved_root not in path.parents:
        raise ConfigError(f"Snapshot manifest path escapes output directory: {relative}")
    return path
