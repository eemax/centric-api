from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..raw_lifecycle import completed_run_dir, raw_run_lifecycle, write_completed_marker
from ..schema import EndpointSchema
from .check import check_raw_run
from .common import (
    RawCheckResult,
    RawCompactResult,
    RawObservation,
    RawWinnerSet,
    _explicit_delete_type,
    _iter_completed_run_paths,
    _manifest_endpoint_records,
    _manifest_endpoint_status,
    _optional_string,
    _utc_iso,
    _verification_seal_matches,
    _write_json,
    canonical_json,
    iter_raw_index,
    load_raw_manifest,
)
from .index import raw_index_manifest_fields
from .observe import _observation_sort_key, load_observation_payloads


def compact_raw_runs(
    *,
    raw_root: Path,
    output_dir: Path | None,
    schemas: dict[str, EndpointSchema],
    archive_old: bool = False,
    dry_run: bool = False,
    exact_counts: bool = False,
) -> RawCompactResult:
    source_runs = _iter_completed_run_paths(raw_root)
    if not source_runs:
        raise ConfigError(f"No completed raw runs found under {raw_root / 'runs'}")
    _validate_compaction_sources(source_runs)
    winner_set = _build_winner_set(source_runs)
    return _compact_raw_runs_from_winners(
        winner_set=winner_set,
        source_runs=source_runs,
        raw_root=raw_root,
        output_dir=output_dir,
        schemas=schemas,
        archive_old=archive_old,
        dry_run=dry_run,
        exact_counts=exact_counts,
    )


def _compact_raw_runs_from_winners(
    *,
    winner_set: RawWinnerSet,
    source_runs: list[Path],
    raw_root: Path,
    output_dir: Path | None,
    schemas: dict[str, EndpointSchema],
    archive_old: bool,
    dry_run: bool,
    exact_counts: bool,
) -> RawCompactResult:
    if winner_set.source_record_count == 0:
        raise ConfigError("No raw index records found. Run a fresh fetch before compacting.")
    winner_count = _winner_count(winner_set)
    output = output_dir or completed_run_dir(raw_root, _compaction_run_id())
    source_runs_to_archive = _archiveable_source_runs(source_runs, output)
    if archive_old and not dry_run:
        _preflight_archive_targets(raw_root, source_runs_to_archive)
    deleted_count: int | None = 0
    written_count: int | None = 0
    counts_exact = not dry_run or exact_counts
    if not dry_run:
        if output.exists():
            raise ConfigError(f"Raw compaction output already exists: {output}")
        try:
            output.mkdir(parents=True)
            endpoint_records: dict[str, dict[str, Any]] = {}
            for endpoint in _winner_endpoints(winner_set):
                raw_path = output / f"{endpoint}.jsonl"
                observations = _iter_winning_observations(winner_set, endpoint=endpoint)
                payloads = load_observation_payloads(observations)
                with raw_path.open("w", encoding="utf-8") as fh:
                    for observation in observations:
                        payload = payloads[observation]
                        schema = schemas.get(endpoint, EndpointSchema(endpoint))
                        if _is_deleted_payload(payload, schema):
                            deleted_count += 1
                            continue
                        fh.write(canonical_json(payload) + "\n")
                        written_count += 1
                index_fields = raw_index_manifest_fields(raw_path, endpoint=endpoint)
                endpoint_records[endpoint] = {
                    "endpoint": endpoint,
                    "file": raw_path.name,
                    "output_file_created": True,
                    "mode": "compacted-full",
                    "status": "OK",
                    "is_delta": False,
                    "items_fetched": index_fields["record_count"],
                    "expected_count": index_fields["record_count"],
                    **index_fields,
                }
            manifest = {
                "schema_version": 2,
                "run_id": output.name,
                "mode": "compacted-full",
                "status": "OK",
                "started_at": _utc_iso(),
                "finished_at": _utc_iso(),
                "output_dir": str(output),
                "selected_endpoints": sorted(endpoint_records),
                "endpoints_total": len(endpoint_records),
                "endpoints_succeeded": len(endpoint_records),
                "endpoints_failed": 0,
                "total_items": written_count,
                "compaction": {
                    "source_runs": [path.name for path in source_runs],
                    "source_run_count": len(source_runs),
                    "source_record_count": winner_set.source_record_count,
                    "winner_count": winner_count,
                    "deleted_winner_count": deleted_count,
                },
                "endpoints": endpoint_records,
            }
            _write_json(output / "manifest.json", manifest)
            write_completed_marker(
                output,
                run_id=output.name,
                mode="compacted-full",
                started_at=str(manifest["started_at"]),
                completed_at=str(manifest["finished_at"]),
                manifest_path=output / "manifest.json",
            )
            _verify_compacted_output(output)
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise
        archived_count = (
            _archive_source_runs(raw_root, source_runs_to_archive) if archive_old else 0
        )
    elif exact_counts:
        for endpoint in _winner_endpoints(winner_set):
            observations = _iter_winning_observations(winner_set, endpoint=endpoint)
            payloads = load_observation_payloads(observations)
            for observation in observations:
                payload = payloads[observation]
                schema = schemas.get(observation.endpoint, EndpointSchema(observation.endpoint))
                if _is_deleted_payload(payload, schema):
                    deleted_count += 1
                else:
                    written_count += 1
        archived_count = 0
    else:
        written_count = None
        deleted_count = None
        archived_count = 0
    return RawCompactResult(
        status="planned" if dry_run else "ok",
        output_dir=output,
        source_run_count=len(source_runs),
        source_record_count=winner_set.source_record_count,
        winner_count=winner_count,
        written_count=written_count,
        deleted_winner_count=deleted_count,
        archived_count=archived_count,
        dry_run=dry_run,
        counts_exact=counts_exact,
    )


def _validate_compaction_sources(source_runs: list[Path]) -> None:
    invalid = [
        result
        for result in (_ensure_raw_run_verified(path) for path in source_runs)
        if result.status != "ok"
    ]
    if not invalid:
        return
    details = ", ".join(f"{result.run_id}={result.status}" for result in invalid[:10])
    remaining = len(invalid) - 10
    suffix = f", ... {remaining} more" if remaining > 0 else ""
    raise ConfigError(f"Raw compaction requires verified indexed runs; found {details}{suffix}.")


def _ensure_raw_run_verified(run_path: Path) -> RawCheckResult:
    manifest = load_raw_manifest(run_path)
    if _verification_seal_matches(run_path, manifest):
        return RawCheckResult(
            run_path=run_path,
            run_id=str(manifest.get("run_id") or run_path.name),
            status="ok",
            lifecycle=raw_run_lifecycle(run_path),
            files=(),
            errors=0,
            warnings=0,
        )
    return check_raw_run(run_path)


def _build_winner_set(source_runs: list[Path]) -> RawWinnerSet:
    winners: dict[str, dict[str, RawObservation]] = defaultdict(dict)
    source_record_count = 0
    for run_path in _sort_source_runs(source_runs):
        manifest = load_raw_manifest(run_path)
        run_id = str(manifest.get("run_id") or run_path.name)
        run_started_at = str(manifest.get("started_at") or "")
        for endpoint, endpoint_record in _manifest_endpoint_records(manifest):
            if _endpoint_is_full_snapshot(manifest, endpoint_record):
                winners[endpoint].clear()
            file_name = endpoint_record.get("file")
            index_name = endpoint_record.get("index_file")
            if not isinstance(file_name, str) or not isinstance(index_name, str):
                continue
            raw_file = run_path / file_name
            index_file = run_path / index_name
            if not index_file.is_file():
                continue
            for record in iter_raw_index(index_file):
                observation = RawObservation(
                    endpoint=endpoint,
                    record_id=str(record["record_id"]),
                    run_id=run_id,
                    run_started_at=run_started_at,
                    raw_file=raw_file,
                    index_file=index_file,
                    line=int(record["line"]),
                    payload_sha256=str(record["payload_sha256"]),
                    raw_line_sha256=str(record["raw_line_sha256"]),
                    modified_at=_optional_string(record.get("modified_at")),
                    delete_type=_optional_string(record.get("delete_type")),
                    manifest_path=run_path / "manifest.json",
                )
                source_record_count += 1
                endpoint_winners = winners[endpoint]
                current = endpoint_winners.get(observation.record_id)
                if current is None or _observation_sort_key(observation) > _observation_sort_key(
                    current
                ):
                    endpoint_winners[observation.record_id] = observation
    return RawWinnerSet(source_record_count=source_record_count, winners=dict(winners))


def _sort_source_runs(source_runs: list[Path]) -> list[Path]:
    return sorted(source_runs, key=_source_run_sort_key)


def _source_run_sort_key(run_path: Path) -> tuple[str, str]:
    manifest = load_raw_manifest(run_path)
    return str(manifest.get("started_at") or ""), run_path.name


def _endpoint_is_full_snapshot(
    manifest: dict[str, Any],
    endpoint_record: dict[str, Any],
) -> bool:
    status = _manifest_endpoint_status(endpoint_record)
    if status in {"FAILED", "PARTIAL", "ERROR"}:
        return False
    mode = str(endpoint_record.get("mode") or manifest.get("mode") or "").lower()
    return endpoint_record.get("is_delta") is False or mode in {"full", "compacted-full"}


def _winner_count(winner_set: RawWinnerSet) -> int:
    return sum(len(records) for records in winner_set.winners.values())


def _winner_endpoints(winner_set: RawWinnerSet) -> list[str]:
    return sorted(winner_set.winners)


def _iter_winning_observations(
    winner_set: RawWinnerSet,
    *,
    endpoint: str,
) -> list[RawObservation]:
    records = winner_set.winners.get(endpoint, {})
    return [records[record_id] for record_id in sorted(records)]


def _is_deleted_payload(payload: dict[str, Any], schema: EndpointSchema) -> bool:
    if _explicit_delete_type(payload):
        return True
    for condition in schema.delete_when_any:
        if payload.get(condition.field) == condition.equals:
            return True
    return False


def _verify_compacted_output(output: Path) -> None:
    result = check_raw_run(output)
    if result.status != "ok":
        messages = [
            error
            for file in result.files
            for error in file.errors
        ]
        joined = "; ".join(messages) if messages else result.status
        raise ConfigError(f"Compacted raw run failed verification: {joined}")


def _archiveable_source_runs(source_runs: list[Path], output: Path) -> list[Path]:
    output_resolved = output.resolve()
    return [source for source in source_runs if source.resolve() != output_resolved]


def _preflight_archive_targets(raw_root: Path, source_runs: list[Path]) -> None:
    archive_root = raw_root / "archive"
    for source in source_runs:
        target = archive_root / source.name
        if target.exists():
            raise ConfigError(f"Raw archive target already exists: {target}")


def _archive_source_runs(raw_root: Path, source_runs: list[Path]) -> int:
    archive_root = raw_root / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = 0
    for source in source_runs:
        target = archive_root / source.name
        shutil.move(str(source), str(target))
        archived += 1
    return archived


def _compaction_run_id() -> str:
    return f"{datetime.now(UTC):%Y-%m-%dT%H%M%SZ}-compacted-full"
