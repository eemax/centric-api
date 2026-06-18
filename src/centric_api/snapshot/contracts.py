from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SnapshotDefinition:
    name: str
    title: str
    required_endpoints: tuple[str, ...] = ()
    description: str = ""
    version: str = "v1"
    group_levels: tuple[str, ...] = ("concept", "season", "brand")


@dataclass(frozen=True)
class SnapshotRecord:
    stream: str
    key: str
    data: dict[str, Any]
    group: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotOutput:
    records: tuple[SnapshotRecord, ...]
    metrics: dict[str, Any] | None = None


@dataclass(frozen=True)
class SnapshotBuildSummary:
    snapshot_name: str
    title: str
    action: str
    status: str
    started_at: str
    finished_at: str
    output_dir: Path | None
    record_count: int
    group_count: int
    stream_count: int
    file_count: int
    manifest_path: Path | None
    metrics: dict[str, Any]


class SnapshotProtocol(Protocol):
    definition: SnapshotDefinition

    def build(self, ctx: Any) -> SnapshotOutput: ...
