from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ._artifact_index import SnapshotArtifactSet, identity_label, read_snapshot_artifacts
from ._json_paths import delete_path, field_changes, get_path, path_exists, set_path
from ._review_file import (
    identity_from_payload,
    load_review_actions,
    review_change_key,
    review_path,
    write_review_file,
)
from .artifacts import write_snapshot_artifacts
from .contracts import (
    SnapshotChange,
    SnapshotDefinition,
    SnapshotDiffSummary,
    SnapshotFieldDiff,
    SnapshotOutput,
    SnapshotRecord,
    SnapshotRecordIdentity,
)


class DefaultSnapshotDiffPolicy:
    record_promotion_streams: frozenset[str] = frozenset()

    def ignored_change_path(
        self,
        identity: SnapshotRecordIdentity,
        path: str,
    ) -> bool:
        return False

    def locked_field_reason(
        self,
        identity: SnapshotRecordIdentity,
        path: str,
    ) -> str | None:
        return None

    def approval_owner(
        self,
        identity: SnapshotRecordIdentity,
        path: str | None,
    ) -> str | None:
        return identity.stream

    def impacts(
        self,
        change: SnapshotChange,
        baseline: SnapshotArtifactSet,
        candidate: SnapshotArtifactSet,
    ) -> tuple[SnapshotRecordIdentity, ...]:
        return ()

    def display_value(
        self,
        _identity: SnapshotRecordIdentity,
        _path: str,
        _value: Any,
        _side: str,
        _context: Any,
    ) -> Any:
        return None

    def record_display(
        self,
        _identity: SnapshotRecordIdentity,
        _baseline: SnapshotArtifactSet,
        _candidate: SnapshotArtifactSet,
        _context: Any | None,
    ) -> Any:
        return None


def diff_snapshot_artifacts(
    *,
    definition: SnapshotDefinition,
    baseline_dir: Path,
    candidate_dir: Path,
    policy: Any | None = None,
    review_file: Path | None = None,
    display_context: Any | None = None,
) -> SnapshotDiffSummary:
    policy = policy or DefaultSnapshotDiffPolicy()
    baseline = read_snapshot_artifacts(baseline_dir)
    candidate = read_snapshot_artifacts(candidate_dir)
    changes = _changes_with_review_displays(
        _changes_with_impacts(
            _diff_artifacts(baseline, candidate, policy),
            policy,
            baseline,
            candidate,
        ),
        policy,
        baseline,
        candidate,
        display_context,
    )
    summary = SnapshotDiffSummary(
        snapshot_name=definition.name,
        title=definition.title,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        changes=changes,
        metrics=_diff_metrics(changes),
    )
    if review_file is not None:
        write_review_file(review_file, summary)
    return summary


def promote_snapshot_review(
    *,
    definition: SnapshotDefinition,
    baseline_dir: Path,
    candidate_dir: Path,
    review_file: Path,
    policy: Any | None = None,
    clean: bool = True,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    policy = policy or DefaultSnapshotDiffPolicy()
    baseline = read_snapshot_artifacts(baseline_dir)
    candidate = read_snapshot_artifacts(candidate_dir)
    current_changes = _changes_with_impacts(
        _diff_artifacts(baseline, candidate, policy),
        policy,
        baseline,
        candidate,
    )
    changes = {review_change_key(change): change for change in current_changes}
    actions = load_review_actions(review_file)
    promoted_record_identities = _promoted_record_identities(actions, changes)
    auto_promoted_locked = _auto_promoted_locked_changes(
        current_changes,
        promoted_record_identities,
    )
    records = {
        identity: copy.deepcopy(record.data) for identity, record in baseline.records.items()
    }
    promoted = 0
    skipped = 0
    for action in actions:
        if action.get("action") != "promote":
            skipped += 1
            continue
        change = _review_action_change(action, changes)
        if change.approval == "locked":
            raise ConfigError(
                "Review action is locked and must be approved through its owner: "
                f"{change.identity.stream}:{change.identity.key} {change.path or '<record>'}"
            )
        _promote_change(records, candidate, change)
        promoted += 1
    for change in auto_promoted_locked:
        _promote_field(records, candidate, change)
    output = SnapshotOutput(
        records=tuple(_snapshot_records_from_artifacts(records)),
        metrics={
            "review_promoted": promoted,
            "review_skipped": skipped,
            "review_auto_promoted_locked": len(auto_promoted_locked),
        },
    )
    manifest_path, manifest = write_snapshot_artifacts(
        baseline_dir,
        definition=definition,
        output=output,
        clean=clean,
    )
    metrics = {
        "promoted": promoted,
        "skipped": skipped,
        "auto_promoted_locked": len(auto_promoted_locked),
    }
    return manifest_path, manifest, metrics


def _diff_artifacts(
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
    policy: Any,
) -> tuple[SnapshotChange, ...]:
    changes: list[SnapshotChange] = []
    identities = sorted(set(baseline.records) | set(candidate.records))
    record_streams = set(getattr(policy, "record_promotion_streams", frozenset()))
    for identity in identities:
        before = baseline.records.get(identity)
        after = candidate.records.get(identity)
        if before is None and after is not None:
            changes.append(_record_change("record_added", identity, after.data, policy))
            continue
        if before is not None and after is None:
            changes.append(_record_change("record_removed", identity, before.data, policy))
            continue
        if before is None or after is None or before.data == after.data:
            continue
        changed_fields = _reviewable_field_changes(
            identity,
            tuple(field_changes(before.data, after.data)),
            policy,
        )
        if not changed_fields:
            continue
        if identity.stream in record_streams:
            changes.append(_record_level_change(identity, changed_fields, policy))
            continue
        changes.extend(_field_level_changes(identity, changed_fields, policy))
    return tuple(changes)


def _record_change(
    change_type: str,
    identity: SnapshotRecordIdentity,
    data: dict[str, Any],
    policy: Any,
) -> SnapshotChange:
    return SnapshotChange(
        change_type=change_type,
        identity=identity,
        old=data if change_type == "record_removed" else None,
        new=data if change_type == "record_added" else None,
        promotion_unit="record",
        approval="actionable",
        approval_owner=_policy_approval_owner(policy, identity, None),
    )


def _record_level_change(
    identity: SnapshotRecordIdentity,
    changed_fields: tuple[tuple[str, Any, Any], ...],
    policy: Any,
) -> SnapshotChange:
    return SnapshotChange(
        change_type="record_changed",
        identity=identity,
        changed_paths=tuple(path for path, _old, _new in changed_fields),
        field_diffs=tuple(
            SnapshotFieldDiff(path=path, old=old, new=new) for path, old, new in changed_fields
        ),
        promotion_unit="record",
        approval="actionable",
        approval_owner=_policy_approval_owner(policy, identity, None),
    )


def _field_level_changes(
    identity: SnapshotRecordIdentity,
    changed_fields: tuple[tuple[str, Any, Any], ...],
    policy: Any,
) -> list[SnapshotChange]:
    changes: list[SnapshotChange] = []
    for path, old, new in changed_fields:
        locked_reason = _policy_locked_reason(policy, identity, path)
        changes.append(
            SnapshotChange(
                change_type="field_changed",
                identity=identity,
                path=path,
                old=old,
                new=new,
                field_diffs=(SnapshotFieldDiff(path=path, old=old, new=new),),
                promotion_unit="field",
                approval="locked" if locked_reason else "actionable",
                approval_owner=_policy_approval_owner(policy, identity, path),
                reason=locked_reason,
            )
        )
    return changes


def _changes_with_impacts(
    changes: tuple[SnapshotChange, ...],
    policy: Any,
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
) -> tuple[SnapshotChange, ...]:
    changes_with_impacts = tuple(
        _with_impacts(change, policy, baseline, candidate)
        for change in sorted(changes, key=_change_sort_key)
    )
    return tuple(sorted(_with_owner_approval_changes(changes_with_impacts), key=_change_sort_key))


def _with_impacts(
    change: SnapshotChange,
    policy: Any,
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
) -> SnapshotChange:
    impacts = _policy_impacts(policy, change, baseline, candidate)
    if not impacts:
        return change
    return replace(change, impacts=tuple(sorted(set(impacts))))


def _with_owner_approval_changes(
    changes: tuple[SnapshotChange, ...],
) -> tuple[SnapshotChange, ...]:
    output = list(changes)
    existing_record_actions = {
        change.identity
        for change in changes
        if change.promotion_unit == "record" and change.path is None
    }
    approval_identities = {
        impact
        for change in changes
        if change.approval == "locked"
        for impact in change.impacts
        if impact not in existing_record_actions
    }
    for identity in sorted(approval_identities):
        output.append(
            SnapshotChange(
                change_type="owner_approval",
                identity=identity,
                promotion_unit="record",
                approval="actionable",
                approval_owner=identity.stream,
                reason="Approves locked changes impacting this record.",
            )
        )
    return tuple(output)


def _changes_with_review_displays(
    changes: tuple[SnapshotChange, ...],
    policy: Any,
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
    display_context: Any | None,
) -> tuple[SnapshotChange, ...]:
    return tuple(
        _with_review_displays(change, policy, baseline, candidate, display_context)
        for change in sorted(changes, key=_change_sort_key)
    )


def _with_review_displays(
    change: SnapshotChange,
    policy: Any,
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
    display_context: Any | None,
) -> SnapshotChange:
    record_display = _policy_record_display(
        policy,
        change.identity,
        baseline,
        candidate,
        display_context,
    )
    impact_displays = tuple(
        _policy_record_display(policy, impact, baseline, candidate, display_context)
        for impact in change.impacts
    )
    old_display = None
    new_display = None
    field_diffs = _field_diffs_with_displays(
        change.field_diffs,
        policy,
        change.identity,
        display_context,
    )
    if change.path is not None and display_context is not None:
        old_display = _policy_display_value(
            policy,
            change.identity,
            change.path,
            change.old,
            "old",
            display_context,
        )
        new_display = _policy_display_value(
            policy,
            change.identity,
            change.path,
            change.new,
            "new",
            display_context,
        )
    if (
        record_display is None
        and old_display is None
        and new_display is None
        and field_diffs == change.field_diffs
        and not any(display is not None for display in impact_displays)
    ):
        return change
    return replace(
        change,
        record_display=record_display,
        old_display=old_display,
        new_display=new_display,
        field_diffs=field_diffs,
        impact_displays=impact_displays,
    )


def _field_diffs_with_displays(
    field_diffs: tuple[SnapshotFieldDiff, ...],
    policy: Any,
    identity: SnapshotRecordIdentity,
    display_context: Any | None,
) -> tuple[SnapshotFieldDiff, ...]:
    if display_context is None or not field_diffs:
        return field_diffs
    output: list[SnapshotFieldDiff] = []
    changed = False
    for field_diff in field_diffs:
        old_display = _policy_display_value(
            policy,
            identity,
            field_diff.path,
            field_diff.old,
            "old",
            display_context,
        )
        new_display = _policy_display_value(
            policy,
            identity,
            field_diff.path,
            field_diff.new,
            "new",
            display_context,
        )
        if old_display is not None or new_display is not None:
            changed = True
            output.append(
                replace(
                    field_diff,
                    old_display=old_display,
                    new_display=new_display,
                )
            )
        else:
            output.append(field_diff)
    return tuple(output) if changed else field_diffs


def _review_action_change(
    action: dict[str, Any],
    changes: dict[tuple[SnapshotRecordIdentity, str | None], SnapshotChange],
) -> SnapshotChange:
    identity = identity_from_payload(action)
    path = review_path(action.get("path"))
    change = changes.get((identity, path))
    if change is None:
        raise ConfigError(
            "Review action does not match a current snapshot diff: "
            f"{identity.stream}:{identity.key} {path or '<record>'}"
        )
    return change


def _promote_change(
    records: dict[SnapshotRecordIdentity, dict[str, Any]],
    candidate: SnapshotArtifactSet,
    change: SnapshotChange,
) -> None:
    if change.change_type == "owner_approval":
        return
    if change.promotion_unit == "record" or change.path is None:
        _promote_record(records, candidate, change)
        return
    _promote_field(records, candidate, change)


def _promote_record(
    records: dict[SnapshotRecordIdentity, dict[str, Any]],
    candidate: SnapshotArtifactSet,
    change: SnapshotChange,
) -> None:
    if change.change_type == "record_removed":
        records.pop(change.identity, None)
        return
    source = candidate.records.get(change.identity)
    if source is None:
        raise ConfigError(f"Candidate record not found: {identity_label(change.identity)}")
    records[change.identity] = copy.deepcopy(source.data)


def _promote_field(
    records: dict[SnapshotRecordIdentity, dict[str, Any]],
    candidate: SnapshotArtifactSet,
    change: SnapshotChange,
) -> None:
    if change.path is None:
        raise ConfigError("Field promotion requires a JSON pointer path.")
    target = records.get(change.identity)
    source = candidate.records.get(change.identity)
    if target is None or source is None:
        raise ConfigError(
            f"Field promotion requires baseline and candidate records: "
            f"{identity_label(change.identity)}"
        )
    if path_exists(source.data, change.path):
        set_path(target, change.path, copy.deepcopy(get_path(source.data, change.path)))
    else:
        delete_path(target, change.path)


def _promoted_record_identities(
    actions: list[dict[str, Any]],
    changes: dict[tuple[SnapshotRecordIdentity, str | None], SnapshotChange],
) -> set[SnapshotRecordIdentity]:
    identities: set[SnapshotRecordIdentity] = set()
    for action in actions:
        if action.get("action") != "promote":
            continue
        identity = identity_from_payload(action)
        path = review_path(action.get("path"))
        change = changes.get((identity, path))
        if change is not None and change.promotion_unit == "record" and path is None:
            identities.add(identity)
    return identities


def _auto_promoted_locked_changes(
    changes: tuple[SnapshotChange, ...],
    promoted_record_identities: set[SnapshotRecordIdentity],
) -> tuple[SnapshotChange, ...]:
    output: list[SnapshotChange] = []
    for change in changes:
        if change.approval != "locked" or not change.impacts:
            continue
        impacts = set(change.impacts)
        approved_impacts = impacts & promoted_record_identities
        if not approved_impacts:
            continue
        if approved_impacts != impacts:
            missing = sorted(impacts - approved_impacts)
            sample = ", ".join(identity_label(identity) for identity in missing[:3])
            raise ConfigError(
                "BOM-impacting material change requires approving every affected "
                f"style-bom before the material field can move: "
                f"{change.identity.stream}:{change.identity.key} {change.path}. "
                f"Missing: {sample}"
            )
        output.append(change)
    return tuple(output)


def _snapshot_records_from_artifacts(
    records: dict[SnapshotRecordIdentity, dict[str, Any]],
) -> list[SnapshotRecord]:
    return [
        SnapshotRecord(
            stream=identity.stream,
            key=identity.key,
            data=data,
            group=identity.group,
        )
        for identity, data in sorted(records.items())
    ]


def _diff_metrics(changes: tuple[SnapshotChange, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "changes": len(changes),
        "actionable": sum(1 for change in changes if change.approval == "actionable"),
        "locked": sum(1 for change in changes if change.approval == "locked"),
    }
    for change in changes:
        metrics[f"{change.change_type}s"] = metrics.get(f"{change.change_type}s", 0) + 1
        metrics[f"stream_{change.identity.stream}"] = (
            metrics.get(f"stream_{change.identity.stream}", 0) + 1
        )
    return metrics


def _change_sort_key(change: SnapshotChange) -> tuple[Any, ...]:
    return (
        change.identity.group,
        change.identity.stream,
        change.identity.key,
        change.path or "",
        change.change_type,
    )


def _policy_locked_reason(
    policy: Any,
    identity: SnapshotRecordIdentity,
    path: str,
) -> str | None:
    method = getattr(policy, "locked_field_reason", None)
    if method is None:
        return None
    reason = method(identity, path)
    return str(reason) if reason else None


def _reviewable_field_changes(
    identity: SnapshotRecordIdentity,
    changed_fields: tuple[tuple[str, Any, Any], ...],
    policy: Any,
) -> tuple[tuple[str, Any, Any], ...]:
    return tuple(
        (path, old, new)
        for path, old, new in changed_fields
        if not _policy_ignored_change_path(policy, identity, path)
    )


def _policy_ignored_change_path(
    policy: Any,
    identity: SnapshotRecordIdentity,
    path: str,
) -> bool:
    method = getattr(policy, "ignored_change_path", None)
    if method is None:
        return False
    return bool(method(identity, path))


def _policy_approval_owner(
    policy: Any,
    identity: SnapshotRecordIdentity,
    path: str | None,
) -> str | None:
    method = getattr(policy, "approval_owner", None)
    if method is None:
        return identity.stream
    owner = method(identity, path)
    return str(owner) if owner else None


def _policy_impacts(
    policy: Any,
    change: SnapshotChange,
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
) -> tuple[SnapshotRecordIdentity, ...]:
    method = getattr(policy, "impacts", None)
    if method is None:
        return ()
    return tuple(method(change, baseline, candidate))


def _policy_display_value(
    policy: Any,
    identity: SnapshotRecordIdentity,
    path: str,
    value: Any,
    side: str,
    display_context: Any,
) -> Any:
    method = getattr(policy, "display_value", None)
    if method is None:
        return None
    return method(identity, path, value, side, display_context)


def _policy_record_display(
    policy: Any,
    identity: SnapshotRecordIdentity,
    baseline: SnapshotArtifactSet,
    candidate: SnapshotArtifactSet,
    display_context: Any | None,
) -> Any:
    method = getattr(policy, "record_display", None)
    if method is None:
        return None
    return method(identity, baseline, candidate, display_context)
