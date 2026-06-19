from __future__ import annotations

from typing import Any

from ..snapshot.contracts import SnapshotBuildSummary, SnapshotDiffSummary, SnapshotProtocol
from .common import format_count


def snapshot_record(snapshot: SnapshotProtocol) -> dict[str, Any]:
    definition = snapshot.definition
    return {
        "name": definition.name,
        "title": definition.title,
        "version": definition.version,
        "group_levels": list(definition.group_levels),
        "required_endpoints": list(definition.required_endpoints),
        "description": definition.description,
    }


def snapshot_summary_record(summary: SnapshotBuildSummary) -> dict[str, Any]:
    return {
        "snapshot": summary.snapshot_name,
        "title": summary.title,
        "action": summary.action,
        "status": summary.status,
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "output_dir": str(summary.output_dir) if summary.output_dir is not None else None,
        "manifest_path": str(summary.manifest_path) if summary.manifest_path is not None else None,
        "records": summary.record_count,
        "groups": summary.group_count,
        "streams": summary.stream_count,
        "files": summary.file_count,
        "metrics": summary.metrics,
    }


def snapshot_diff_record(summary: SnapshotDiffSummary) -> dict[str, Any]:
    return {
        "snapshot": summary.snapshot_name,
        "title": summary.title,
        "baseline_dir": str(summary.baseline_dir),
        "candidate_dir": str(summary.candidate_dir),
        "metrics": summary.metrics,
        "changes": [
            {
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
                    {
                        "stream": impact.stream,
                        "group": list(impact.group),
                        "key": impact.key,
                        "display": (
                            change.impact_displays[index]
                            if index < len(change.impact_displays)
                            else None
                        ),
                    }
                    for index, impact in enumerate(change.impacts)
                ],
                "old": change.old,
                "new": change.new,
                "old_display": change.old_display,
                "new_display": change.new_display,
                "changed_paths": list(change.changed_paths),
                "field_diffs": [
                    {
                        "path": field_diff.path,
                        "old": field_diff.old,
                        "new": field_diff.new,
                        "old_display": field_diff.old_display,
                        "new_display": field_diff.new_display,
                    }
                    for field_diff in change.field_diffs
                ],
            }
            for change in summary.changes
        ],
    }


def print_human_snapshot_list(snapshots: tuple[SnapshotProtocol, ...]) -> None:
    print("Snapshots")
    print()
    print(f"Snapshots: {format_count(len(snapshots))}")
    if not snapshots:
        return
    print()
    name_width = max(len("Name"), *(len(snapshot.definition.name) for snapshot in snapshots))
    version_width = max(
        len("Version"), *(len(snapshot.definition.version) for snapshot in snapshots)
    )
    header = f"{'Name':<{name_width}}  {'Version':<{version_width}}  Title"
    print(header)
    print("-" * len(header))
    for snapshot in snapshots:
        print(
            f"{snapshot.definition.name:<{name_width}}  "
            f"{snapshot.definition.version:<{version_width}}  "
            f"{snapshot.definition.title}"
        )


def print_human_snapshot_show(snapshot: SnapshotProtocol) -> None:
    definition = snapshot.definition
    print(f"Snapshot: {definition.name}")
    print()
    print(f"Title:   {definition.title}")
    print(f"Version: {definition.version}")
    print(f"Groups:  {' / '.join(definition.group_levels)}")
    if definition.description:
        print(f"About:   {definition.description}")
    if definition.required_endpoints:
        print(f"Needs:   {', '.join(definition.required_endpoints)}")


def print_human_snapshot_summary(summary: SnapshotBuildSummary) -> None:
    labels = {
        "check": "Snapshot check",
        "build": "Snapshot build",
        "promote": "Snapshot promote",
    }
    label = labels.get(summary.action, f"Snapshot {summary.action}")
    print(f"{label}: {summary.snapshot_name}")
    print()
    print(f"Status:   {summary.status}")
    print(f"Records:  {format_count(summary.record_count)}")
    print(f"Groups:   {format_count(summary.group_count)}")
    print(f"Streams:  {format_count(summary.stream_count)}")
    print(f"Files:    {format_count(summary.file_count)}")
    if summary.output_dir is not None:
        print(f"Output:   {summary.output_dir}")
    if summary.manifest_path is not None:
        print(f"Manifest: {summary.manifest_path}")
    _print_metrics(summary)


def print_human_snapshot_diff(summary: SnapshotDiffSummary) -> None:
    print(f"Snapshot diff: {summary.snapshot_name}")
    print()
    print(f"Baseline:  {summary.baseline_dir}")
    print(f"Candidate: {summary.candidate_dir}")
    print(f"Changes:   {format_count(summary.metrics.get('changes', 0))}")
    print(f"Actionable:{format_count(summary.metrics.get('actionable', 0)):>8}")
    print(f"Locked:    {format_count(summary.metrics.get('locked', 0)):>8}")
    if not summary.changes:
        return
    print()
    print("Changes")
    print(
        f"{'Approval':<10}  {'Unit':<6}  {'Stream':<11}  {'Group':<28}  {'Key':<32}  Path / Impact"
    )
    print("-" * 112)
    for change in summary.changes[:200]:
        group = "/".join(change.identity.group)
        impact = ""
        if change.impacts:
            impact = f" -> impacts {len(change.impacts)} {change.approval_owner or 'records'}"
        path = change.path or "<record>"
        reason = f" ({change.reason})" if change.reason else ""
        print(
            f"{change.approval:<10}  {change.promotion_unit:<6}  "
            f"{change.identity.stream:<11}  {_clip(group, 28):<28}  "
            f"{_clip(change.identity.key, 32):<32}  {_clip(path + impact + reason, 80)}"
        )
    if len(summary.changes) > 200:
        print(f"... {format_count(len(summary.changes) - 200)} more changes")


def _print_metrics(summary: SnapshotBuildSummary) -> None:
    if not summary.metrics:
        return
    print()
    print("Metrics")
    for key, value in summary.metrics.items():
        label = key.replace("_", " ").title()
        if isinstance(value, int):
            value = format_count(value)
        print(f"  {label}: {value}")


def _clip(value: object, width: int) -> str:
    text = str(value or "")
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"
