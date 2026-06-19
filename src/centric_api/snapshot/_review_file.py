from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ._artifact_index import load_json_object
from .contracts import SnapshotChange, SnapshotDiffSummary, SnapshotRecordIdentity

REVIEW_SCHEMA_VERSION = 1
REVIEW_ACTIONS = frozenset({"promote", "skip"})


def write_review_file(path: Path, summary: SnapshotDiffSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "snapshot": summary.snapshot_name,
        "baseline_dir": str(summary.baseline_dir),
        "candidate_dir": str(summary.candidate_dir),
        "metrics": summary.metrics,
        "actions": [review_action(change) for change in summary.changes],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_review_actions(path: Path) -> list[dict[str, Any]]:
    payload = load_json_object(path)
    version = payload.get("schema_version")
    if version != REVIEW_SCHEMA_VERSION:
        raise ConfigError(
            f"Snapshot review file schema_version must be {REVIEW_SCHEMA_VERSION}: {path}"
        )
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ConfigError(f"Snapshot review file must contain actions: {path}")
    output: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ConfigError(f"Snapshot review action {index} must be an object: {path}")
        action_value = action.get("action")
        if action_value not in REVIEW_ACTIONS:
            choices = ", ".join(sorted(REVIEW_ACTIONS))
            raise ConfigError(
                f"Snapshot review action {index} action must be one of {choices}: {path}"
            )
        output.append(action)
    return output


def identity_from_payload(payload: dict[str, Any]) -> SnapshotRecordIdentity:
    stream = str(payload.get("stream") or "").strip()
    key = str(payload.get("key") or "").strip()
    group_value = payload.get("group") or []
    if not isinstance(group_value, list):
        raise ConfigError("Snapshot review action group must be a list.")
    if not stream or not key:
        raise ConfigError("Snapshot review action must include stream and key.")
    return SnapshotRecordIdentity(
        group=tuple(str(part) for part in group_value),
        stream=stream,
        key=key,
    )


def review_change_key(change: SnapshotChange) -> tuple[SnapshotRecordIdentity, str | None]:
    return change.identity, review_path(change.path)


def review_path(path: Any) -> str | None:
    text = str(path or "").strip()
    return text or None


def review_action(change: SnapshotChange) -> dict[str, Any]:
    return {
        "action": "skip",
        "change_type": change.change_type,
        "stream": change.identity.stream,
        "group": list(change.identity.group),
        "key": change.identity.key,
        "path": change.path,
        "promotion_unit": change.promotion_unit,
        "approval": change.approval,
        "approval_owner": change.approval_owner,
        "reason": change.reason,
        "record_display": change.record_display,
        "impacts": [
            _identity_payload(identity, display=display)
            for identity, display in _impacts_with_displays(change)
        ],
        "old": change.old,
        "new": change.new,
        "old_display": change.old_display,
        "new_display": change.new_display,
        "changed_paths": list(change.changed_paths),
        "field_diffs": [_field_diff_payload(field_diff) for field_diff in change.field_diffs],
    }


def _impacts_with_displays(
    change: SnapshotChange,
) -> tuple[tuple[SnapshotRecordIdentity, Any], ...]:
    displays = list(change.impact_displays)
    if len(displays) < len(change.impacts):
        displays.extend([None] * (len(change.impacts) - len(displays)))
    return tuple(zip(change.impacts, displays, strict=False))


def _identity_payload(
    identity: SnapshotRecordIdentity,
    *,
    display: Any = None,
) -> dict[str, Any]:
    payload = {
        "stream": identity.stream,
        "group": list(identity.group),
        "key": identity.key,
    }
    if display is not None:
        payload["display"] = display
    return payload


def _field_diff_payload(field_diff: Any) -> dict[str, Any]:
    return {
        "path": field_diff.path,
        "old": field_diff.old,
        "new": field_diff.new,
        "old_display": field_diff.old_display,
        "new_display": field_diff.new_display,
    }
