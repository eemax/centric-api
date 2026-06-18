from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from ..config import ConfigError, runtime_home
from .artifacts import stream_filename
from .contracts import SnapshotDefinition, SnapshotOutput, SnapshotProtocol, SnapshotRecord

PRIVATE_SNAPSHOTS_DIR = Path("snapshots")
_SNAPSHOT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def private_snapshots_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return runtime_home() / PRIVATE_SNAPSHOTS_DIR


def discover_snapshots(path: str | Path | None = None) -> tuple[SnapshotProtocol, ...]:
    snapshots: dict[str, SnapshotProtocol] = {}
    directory = private_snapshots_dir(path)
    if directory.is_dir():
        for snapshot_path in sorted(directory.glob("*.py")):
            if snapshot_path.name.startswith("_"):
                continue
            snapshot = _load_snapshot(snapshot_path)
            if snapshot.definition.name in snapshots:
                raise ConfigError(f"Duplicate private snapshot name: {snapshot.definition.name}")
            snapshots[snapshot.definition.name] = snapshot
    return tuple(snapshots[name] for name in sorted(snapshots))


def select_snapshot(
    snapshots: tuple[SnapshotProtocol, ...],
    name: str,
) -> SnapshotProtocol:
    for snapshot in snapshots:
        if snapshot.definition.name == name:
            return snapshot
    names = ", ".join(snapshot.definition.name for snapshot in snapshots)
    if names:
        raise ConfigError(f"Unknown snapshot {name!r}. Available: {names}")
    raise ConfigError("No snapshots found.")


def _load_snapshot(path: Path) -> SnapshotProtocol:
    module_name = f"centric_api_private_snapshot_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load snapshot file: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ConfigError(f"Could not import snapshot file {path}: {exc}") from exc
    snapshot = _module_snapshot(module)
    _validate_snapshot(snapshot, path)
    return snapshot


def _module_snapshot(module: Any) -> Any:
    if hasattr(module, "get_snapshot"):
        return module.get_snapshot()
    if hasattr(module, "SNAPSHOT"):
        return module.SNAPSHOT
    raise ConfigError("Snapshot file must expose SNAPSHOT or get_snapshot().")


def _validate_snapshot(snapshot: Any, path: Path) -> None:
    definition = getattr(snapshot, "definition", None)
    if not isinstance(definition, SnapshotDefinition):
        raise ConfigError(f"Snapshot {path} must expose a SnapshotDefinition as definition.")
    if not isinstance(definition.name, str) or not definition.name.strip():
        raise ConfigError(f"Snapshot {path} definition.name must be a non-empty string.")
    if not _SNAPSHOT_NAME_RE.fullmatch(definition.name):
        raise ConfigError(
            f"Snapshot {path} definition.name must contain only letters, digits, "
            "dot, dash, or underscore and must start with a letter or digit."
        )
    if not isinstance(definition.title, str) or not definition.title.strip():
        raise ConfigError(f"Snapshot {path} definition.title must be a non-empty string.")
    if isinstance(definition.required_endpoints, str) or not isinstance(
        definition.required_endpoints, tuple
    ):
        raise ConfigError(f"Snapshot {definition.name} required_endpoints must be a tuple.")
    if isinstance(definition.group_levels, str) or not isinstance(definition.group_levels, tuple):
        raise ConfigError(f"Snapshot {definition.name} group_levels must be a tuple.")
    if not callable(getattr(snapshot, "build", None)):
        raise ConfigError(f"Snapshot {definition.name} must implement build(ctx).")


def validate_snapshot_output(snapshot_name: str, output: object) -> SnapshotOutput:
    if not isinstance(output, SnapshotOutput):
        raise ConfigError(f"Snapshot {snapshot_name} must return SnapshotOutput.")
    seen: set[tuple[tuple[str, ...], str, str]] = set()
    for record in output.records:
        if not isinstance(record, SnapshotRecord):
            raise ConfigError(f"Snapshot {snapshot_name} records must be SnapshotRecord items.")
        identity = (record.group, stream_filename(record.stream), record.key)
        if identity in seen:
            group = "/".join(record.group) or "."
            raise ConfigError(
                f"Snapshot {snapshot_name} emitted duplicate record "
                f"{group}/{record.stream}:{record.key}."
            )
        seen.add(identity)
    return output
