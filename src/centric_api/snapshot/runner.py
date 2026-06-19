from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..config import ConfigError, runtime_home
from ..store import connect_readonly
from ..units import load_unit_registry
from ._review_display import SnapshotReviewDisplayContext
from .artifacts import copy_snapshot_artifacts, stream_filename, write_snapshot_artifacts
from .context import SnapshotContext
from .contracts import SnapshotBuildSummary, SnapshotDiffSummary, SnapshotOutput, SnapshotProtocol
from .diffing import diff_snapshot_artifacts, promote_snapshot_review
from .registry import validate_snapshot_output

DEFAULT_SNAPSHOT_WORKSPACE_DIR = Path("snapshot/workspaces")
SNAPSHOT_TARGETS = ("candidate", "baseline")


def check_snapshot(
    db_path: Path,
    snapshot: SnapshotProtocol,
    *,
    units_config: str | Path | None = None,
) -> SnapshotBuildSummary:
    started_at = _utc_iso()
    output = _build_output(db_path, snapshot, units_config=units_config)
    return _summary(
        snapshot,
        output=output,
        action="check",
        status="ok",
        started_at=started_at,
        finished_at=_utc_iso(),
        output_dir=None,
        manifest_path=None,
    )


def build_snapshot(
    db_path: Path,
    snapshot: SnapshotProtocol,
    *,
    output_root: str | Path | None = None,
    target: str = "candidate",
    units_config: str | Path | None = None,
    clean: bool = False,
) -> SnapshotBuildSummary:
    started_at = _utc_iso()
    output = _build_output(db_path, snapshot, units_config=units_config)
    output_dir = _output_dir(snapshot.definition.name, output_root, target)
    manifest_path, _manifest = write_snapshot_artifacts(
        output_dir,
        definition=snapshot.definition,
        output=output,
        clean=clean,
    )
    return _summary(
        snapshot,
        output=output,
        action="build",
        status="ok",
        started_at=started_at,
        finished_at=_utc_iso(),
        output_dir=output_dir,
        manifest_path=manifest_path,
    )


def promote_snapshot(
    snapshot: SnapshotProtocol,
    *,
    output_root: str | Path | None = None,
    clean: bool = False,
    review_file: str | Path | None = None,
) -> SnapshotBuildSummary:
    started_at = _utc_iso()
    source_dir = _output_dir(snapshot.definition.name, output_root, "candidate")
    output_dir = _output_dir(snapshot.definition.name, output_root, "baseline")
    if review_file is None:
        manifest_path, manifest = copy_snapshot_artifacts(source_dir, output_dir, clean=clean)
        metrics = dict(manifest.get("metrics") or {})
    else:
        manifest_path, manifest, review_metrics = promote_snapshot_review(
            definition=snapshot.definition,
            baseline_dir=output_dir,
            candidate_dir=source_dir,
            review_file=Path(review_file).expanduser(),
            policy=_snapshot_diff_policy(snapshot),
            clean=clean,
        )
        metrics = {**dict(manifest.get("metrics") or {}), **review_metrics}
    return SnapshotBuildSummary(
        snapshot_name=snapshot.definition.name,
        title=snapshot.definition.title,
        action="promote",
        status="ok",
        started_at=started_at,
        finished_at=_utc_iso(),
        output_dir=output_dir,
        record_count=int(manifest.get("record_count") or 0),
        group_count=int(manifest.get("group_count") or 0),
        stream_count=int(manifest.get("stream_count") or 0),
        file_count=int(manifest.get("file_count") or 0),
        manifest_path=manifest_path,
        metrics=metrics,
    )


def diff_snapshot(
    snapshot: SnapshotProtocol,
    *,
    output_root: str | Path | None = None,
    review_file: str | Path | None = None,
    db_path: Path | None = None,
    require_db: bool = False,
) -> SnapshotDiffSummary:
    baseline_dir = _output_dir(snapshot.definition.name, output_root, "baseline")
    candidate_dir = _output_dir(snapshot.definition.name, output_root, "candidate")
    if db_path is not None and (db_path.exists() or require_db):
        with connect_readonly(db_path) as conn:
            return diff_snapshot_artifacts(
                definition=snapshot.definition,
                baseline_dir=baseline_dir,
                candidate_dir=candidate_dir,
                policy=_snapshot_diff_policy(snapshot),
                review_file=Path(review_file).expanduser() if review_file is not None else None,
                display_context=SnapshotReviewDisplayContext(conn),
            )
    return diff_snapshot_artifacts(
        definition=snapshot.definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_snapshot_diff_policy(snapshot),
        review_file=Path(review_file).expanduser() if review_file is not None else None,
    )


def _build_output(
    db_path: Path,
    snapshot: SnapshotProtocol,
    *,
    units_config: str | Path | None,
) -> SnapshotOutput:
    with connect_readonly(db_path) as conn:
        ctx = SnapshotContext(
            conn,
            units=load_unit_registry(units_config),
            snapshot_name=snapshot.definition.name,
        )
        for endpoint in snapshot.definition.required_endpoints:
            ctx.resolve_endpoint(endpoint)
        output = snapshot.build(ctx)
    return validate_snapshot_output(snapshot.definition.name, output)


def _output_dir(snapshot_name: str, output_root: str | Path | None, target: str) -> Path:
    if target not in SNAPSHOT_TARGETS:
        choices = ", ".join(SNAPSHOT_TARGETS)
        raise ConfigError(f"Snapshot target must be one of: {choices}.")
    root = (
        Path(output_root).expanduser()
        if output_root is not None
        else runtime_home() / DEFAULT_SNAPSHOT_WORKSPACE_DIR
    )
    return root / snapshot_name / target


def _summary(
    snapshot: SnapshotProtocol,
    *,
    output: SnapshotOutput,
    action: str,
    status: str,
    started_at: str,
    finished_at: str,
    output_dir: Path | None,
    manifest_path: Path | None,
) -> SnapshotBuildSummary:
    groups = {record.group for record in output.records}
    streams = {stream_filename(record.stream) for record in output.records}
    files = {(record.group, stream_filename(record.stream)) for record in output.records}
    return SnapshotBuildSummary(
        snapshot_name=snapshot.definition.name,
        title=snapshot.definition.title,
        action=action,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        output_dir=output_dir,
        record_count=len(output.records),
        group_count=len(groups),
        stream_count=len(streams),
        file_count=len(files),
        manifest_path=manifest_path,
        metrics=output.metrics or {},
    )


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _snapshot_diff_policy(snapshot: SnapshotProtocol) -> object | None:
    factory = getattr(snapshot, "diff_policy", None)
    if callable(factory):
        return factory()
    policy = getattr(snapshot, "promotion_policy", None)
    if policy is not None:
        return policy
    return None
