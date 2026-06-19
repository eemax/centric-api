from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..config import ConfigError, runtime_path
from ..raw_evidence import (
    RawCheckFile,
    RawCheckResult,
    RawIndexRunResult,
    RawObservation,
    check_raw_runs,
    choose_diff_observations,
    compact_raw_runs,
    diff_payloads,
    find_raw_observations,
    index_raw_runs,
    load_observation_payload,
)
from ..schema import load_endpoint_schemas


def run_raw_command(args: argparse.Namespace) -> int:
    raw_root = (
        Path(args.raw_dir).expanduser() if getattr(args, "raw_dir", None) else runtime_path("raw")
    )
    if args.action == "check":
        results = check_raw_runs(raw_root, args.raw_run)
        payload = {
            "status": _check_status(results),
            "raw_dir": str(raw_root),
            "runs": [_check_result_record(result) for result in results],
            "run_count": len(results),
            "errors": sum(result.errors for result in results),
            "warnings": sum(result.warnings for result in results),
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_check_results(results, payload)
        return 1 if payload["status"] == "failed" else 0

    if args.action == "index":
        result = index_raw_runs(
            raw_root=raw_root,
            raw_run=args.raw_run,
            all_runs=args.all,
        )
        payload = {
            "status": result.status,
            "raw_dir": str(result.raw_root),
            "run_count": len(result.runs),
            "indexed_files": result.indexed_files,
            "skipped_files": result.skipped_files,
            "runs": [_index_result_record(run) for run in result.runs],
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_index_results(payload)
        return 1 if payload["status"] == "failed" else 0

    if args.action == "inspect":
        observations = _filtered_observations(
            endpoint=args.endpoint,
            record_id=args.record_id,
            raw_root=raw_root,
            payload_hash=args.hash,
        )
        if args.latest and observations:
            observations = [observations[-1]]
        payload = {
            "endpoint": args.endpoint,
            "record_id": args.record_id,
            "matches": len(observations),
            "observations": [
                _observation_record(observation, show_payload=args.show_payload)
                for observation in observations
            ],
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_inspect(payload)
        return 0

    if args.action == "diff":
        observations = _filtered_observations(
            endpoint=args.endpoint,
            record_id=args.record_id,
            raw_root=raw_root,
            payload_hash=None,
        )
        left, right = choose_diff_observations(
            observations,
            from_hash=args.from_hash,
            to_hash=args.to_hash,
        )
        left_payload = load_observation_payload(left)
        right_payload = load_observation_payload(right)
        changes = diff_payloads(left_payload, right_payload)
        payload = {
            "endpoint": args.endpoint,
            "record_id": args.record_id,
            "from": _observation_record(left, show_payload=False),
            "to": _observation_record(right, show_payload=False),
            "change_count": len(changes),
            "changes": changes,
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_diff(payload)
        return 0

    if args.action == "compact":
        if args.exact and not args.dry_run:
            raise ConfigError("--exact is only needed with raw compact --dry-run.")
        schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
        result = compact_raw_runs(
            raw_root=raw_root,
            output_dir=Path(args.output).expanduser() if args.output else None,
            schemas=schemas,
            archive_old=args.archive_old,
            dry_run=args.dry_run,
            exact_counts=args.exact,
        )
        payload = {
            "status": result.status,
            "raw_dir": str(raw_root),
            "output_dir": str(result.output_dir),
            "source_run_count": result.source_run_count,
            "source_record_count": result.source_record_count,
            "winner_count": result.winner_count,
            "written_count": result.written_count,
            "deleted_winner_count": result.deleted_winner_count,
            "archived_count": result.archived_count,
            "dry_run": result.dry_run,
            "counts_exact": result.counts_exact,
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_compact(payload)
        return 0

    raise ConfigError(f"Unknown raw action: {args.action}")


def _filtered_observations(
    *,
    endpoint: str,
    record_id: str,
    raw_root: Path,
    payload_hash: str | None,
) -> list[RawObservation]:
    observations = find_raw_observations(
        endpoint=endpoint,
        record_id=record_id,
        raw_root=raw_root,
    )
    if payload_hash:
        matches = [item for item in observations if item.payload_sha256.startswith(payload_hash)]
        if not matches:
            raise ConfigError(f"No raw observation matched hash: {payload_hash}")
        return matches
    if not observations:
        raise ConfigError(f"No raw observations found for {endpoint}/{record_id}.")
    return observations


def _check_status(results: tuple[RawCheckResult, ...]) -> str:
    if any(result.status == "failed" for result in results):
        return "failed"
    if any(result.status == "warn" for result in results):
        return "warn"
    return "ok"


def _check_result_record(result: RawCheckResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "path": str(result.run_path),
        "status": result.status,
        "lifecycle": result.lifecycle,
        "errors": result.errors,
        "warnings": result.warnings,
        "files": [_check_file_record(file) for file in result.files],
    }


def _check_file_record(file: RawCheckFile) -> dict[str, Any]:
    return {
        "endpoint": file.endpoint,
        "path": str(file.file),
        "index_path": str(file.index_file) if file.index_file else None,
        "status": file.status,
        "content_sha256": file.content_sha256,
        "expected_content_sha256": file.expected_content_sha256,
        "index_sha256": file.index_sha256,
        "expected_index_sha256": file.expected_index_sha256,
        "line_count": file.line_count,
        "expected_line_count": file.expected_line_count,
        "byte_size": file.byte_size,
        "expected_byte_size": file.expected_byte_size,
        "record_count": file.record_count,
        "expected_record_count": file.expected_record_count,
        "invalid_records": file.invalid_records,
        "errors": list(file.errors),
        "warnings": list(file.warnings),
    }


def _index_result_record(run: RawIndexRunResult) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "path": str(run.run_path),
        "status": run.status,
        "indexed_files": run.indexed_files,
        "skipped_files": run.skipped_files,
        "errors": list(run.errors),
    }


def _observation_record(
    observation: RawObservation,
    *,
    show_payload: bool,
) -> dict[str, Any]:
    record = {
        "endpoint": observation.endpoint,
        "record_id": observation.record_id,
        "run_id": observation.run_id,
        "run_started_at": observation.run_started_at,
        "file": str(observation.raw_file),
        "index_file": str(observation.index_file),
        "line": observation.line,
        "payload_sha256": observation.payload_sha256,
        "raw_line_sha256": observation.raw_line_sha256,
        "modified_at": observation.modified_at,
        "delete_type": observation.delete_type,
        "manifest_path": str(observation.manifest_path),
    }
    if show_payload:
        record["payload"] = load_observation_payload(observation)
    return record


def _print_check_results(results: tuple[RawCheckResult, ...], payload: dict[str, Any]) -> None:
    print("Raw Evidence Check")
    print()
    print(f"Status:   {payload['status']}")
    print(f"Runs:     {payload['run_count']}")
    print(f"Errors:   {payload['errors']}")
    print(f"Warnings: {payload['warnings']}")
    print()
    print("Run                            Status  Files  Errors  Warnings")
    print("----------------------------------------------------------------")
    for result in results:
        print(
            f"{result.run_id:<30} {result.status:<7} "
            f"{len(result.files):>5} {result.errors:>7} {result.warnings:>9}"
        )
    if not results:
        print("No raw runs found.")


def _print_index_results(payload: dict[str, Any]) -> None:
    print("Raw Evidence Index")
    print()
    print(f"Status:   {payload['status']}")
    print(f"Runs:     {payload['run_count']}")
    print(f"Indexed:  {payload['indexed_files']}")
    print(f"Skipped:  {payload['skipped_files']}")
    print()
    print("Run                            Status  Indexed  Skipped")
    print("--------------------------------------------------------")
    for run in payload["runs"]:
        print(
            f"{run['run_id']:<30} {run['status']:<7} "
            f"{run['indexed_files']:>7} {run['skipped_files']:>8}"
        )
        for error in run["errors"][:3]:
            print(f"  - {error}")
        remaining = len(run["errors"]) - 3
        if remaining > 0:
            print(f"  ... {remaining} more errors")


def _print_inspect(payload: dict[str, Any]) -> None:
    print("Raw Evidence")
    print()
    print(f"Endpoint: {payload['endpoint']}")
    print(f"Record:   {payload['record_id']}")
    print(f"Matches:  {payload['matches']}")
    observations = payload["observations"]
    if observations:
        latest = observations[-1]
        print()
        print("Latest")
        _print_observation(latest)
    if len(observations) > 1:
        print()
        print("History")
        for observation in observations:
            print(
                f"{observation['run_id']}  line {observation['line']}  "
                f"{observation['payload_sha256'][:12]}  "
                f"modified {observation['modified_at'] or '-'}"
            )


def _print_diff(payload: dict[str, Any]) -> None:
    print("Raw Diff")
    print()
    print(f"Endpoint: {payload['endpoint']}")
    print(f"Record:   {payload['record_id']}")
    print()
    print("From")
    _print_observation(payload["from"])
    print()
    print("To")
    _print_observation(payload["to"])
    print()
    print("Changed Fields")
    if not payload["changes"]:
        print("No payload changes.")
        return
    for change in payload["changes"][:50]:
        print(
            f"{change['path']}: {_compact_value(change['from'])} -> {_compact_value(change['to'])}"
        )
    remaining = payload["change_count"] - 50
    if remaining > 0:
        print(f"... {remaining} more changes")


def _print_compact(payload: dict[str, Any]) -> None:
    print("Raw Compaction")
    print()
    print(f"Status:    {payload['status']}")
    print(f"Output:    {payload['output_dir']}")
    print(f"Sources:   {payload['source_run_count']}")
    print(f"Indexed:   {payload['source_record_count']}")
    print(f"Winners:   {payload['winner_count']}")
    print(f"Written:   {_compact_count(payload['written_count'])}")
    print(f"Deleted:   {_compact_count(payload['deleted_winner_count'])}")
    print(f"Archived:  {payload['archived_count']}")
    if payload["dry_run"]:
        print()
        print("Dry run only; no files were written.")
        if not payload["counts_exact"]:
            print("Use --exact to count written/deleted winners.")


def _print_observation(observation: dict[str, Any]) -> None:
    print(f"Run:      {observation['run_id']}")
    print(f"File:     {observation['file']}")
    print(f"Line:     {observation['line']}")
    print(f"Hash:     {observation['payload_sha256']}")
    print(f"Modified: {observation['modified_at'] or '-'}")


def _compact_value(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) > 90:
        return f"{text[:87]}..."
    return text


def _compact_count(value: Any) -> str:
    return "not calculated" if value is None else str(value)


__all__ = ["run_raw_command"]
