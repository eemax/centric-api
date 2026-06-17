from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .diff import diff_swagger_indexes
from .index import build_swagger_index
from .loading import (
    load_swagger_document,
    resolve_swagger_history_dir,
    resolve_swagger_history_meta_path,
)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_snapshot_id(timestamp: str) -> str:
    return timestamp.replace(":", "").replace("-", "").replace(".", "").removesuffix("Z")


def history_diff_snapshots(indexes: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    current_index, baseline_index = indexes
    rows = history_snapshots()
    if not rows:
        raise ValueError(f"Swagger history is empty: {resolve_swagger_history_dir()}")
    max_index = len(rows) - 1
    if current_index > max_index or baseline_index > max_index:
        raise ValueError(f"Swagger history index out of range. Available indexes: 0..{max_index}.")
    if current_index == baseline_index:
        raise ValueError("Swagger history indexes must be different.")
    return rows[current_index], rows[baseline_index]


def history_snapshots() -> list[dict[str, Any]]:
    history_dir = resolve_swagger_history_dir()
    if not history_dir.is_dir():
        return []
    snapshot_paths = sorted(
        (path for path in history_dir.glob("*.json") if not path.name.endswith(".meta.json")),
        key=lambda path: path.stem,
        reverse=True,
    )
    rows = [_history_snapshot_row(index, path) for index, path in enumerate(snapshot_paths)]
    return rows


def history_diff_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diff_rows = []
    for current_snapshot, baseline_snapshot in zip(rows, rows[1:], strict=False):
        current = build_swagger_index(load_swagger_document(current_snapshot["path"]))
        baseline = build_swagger_index(load_swagger_document(baseline_snapshot["path"]))
        diff = diff_swagger_indexes(baseline, current)
        diff_rows.append(
            {
                "current_index": current_snapshot["index"],
                "current_snapshot_id": current_snapshot["snapshot_id"],
                "current_path": current_snapshot["path"],
                "baseline_index": baseline_snapshot["index"],
                "baseline_snapshot_id": baseline_snapshot["snapshot_id"],
                "baseline_path": baseline_snapshot["path"],
                "field_added_count": diff["field_added_count"],
                "field_removed_count": diff["field_removed_count"],
                "field_changed_count": diff["field_changed_count"],
                "operation_added_count": diff["operation_added_count"],
                "operation_removed_count": diff["operation_removed_count"],
                "operation_changed_count": diff["operation_changed_count"],
                "added_count": diff["added_count"],
                "removed_count": diff["removed_count"],
                "changed_count": diff["changed_count"],
            }
        )
    return diff_rows


def _history_snapshot_row(index: int, path: Path) -> dict[str, Any]:
    meta_path = resolve_swagger_history_meta_path(path.stem)
    meta = _load_json_object(meta_path)
    return {
        "index": index,
        "snapshot_id": path.stem,
        "path": str(path),
        "meta_path": str(meta_path),
        "fetched_at": meta.get("fetched_at"),
        "swagger_version": meta.get("swagger_version"),
        "operation_count": meta.get("operation_count"),
        "endpoint_count": meta.get("endpoint_count"),
        "field_schema_count": meta.get("field_schema_count"),
        "field_count": meta.get("field_count"),
        "sha256": meta.get("sha256"),
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
