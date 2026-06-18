from __future__ import annotations

from typing import Any

from ..snapshot.contracts import SnapshotBuildSummary, SnapshotProtocol
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
