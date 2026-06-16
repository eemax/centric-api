from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._store.discovery import _sha256
from ..changelog import ChangelogRun
from ..config import ConfigError, runtime_path
from ..defaults import db_path as resolve_db_path
from ..rendering.logs import format_duration
from ..schema import load_endpoint_schemas
from ..store import (
    IngestResult,
    connect_readonly,
    discover_raw_files,
    ingest_raw_dir,
    table_exists,
)
from .pipeline import run_changelog_after_ingest

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class _RawRun:
    path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    files: list[_RawRunFile]


@dataclass(frozen=True)
class _RawRunFile:
    endpoint: str
    path: Path
    expected_count: int | None
    is_delta: bool
    content_sha256: str | None
    line_count: int | None
    invalid_json_lines: int
    known_schema: bool
    applied_state: str


def run_ingest_command(args: argparse.Namespace) -> int:
    if args.action == "check":
        return _run_check(args)
    if args.action == "raw-run":
        return _run_raw_run(args)
    raise ConfigError(f"Unknown ingest action: {args.action}")


def _run_check(args: argparse.Namespace) -> int:
    started = time.time()
    db_path = resolve_db_path(args.db)
    schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
    raw_run = _inspect_raw_run(args.raw_run, db_path=db_path, known_endpoints=set(schemas))
    payload = _check_payload(raw_run, db_path=db_path, elapsed_seconds=time.time() - started)
    if args.json:
        print(json.dumps(payload, default=str))
    else:
        _print_check(raw_run, payload)
    return 0 if payload["status"] in {"ok", "warn"} else 1


def _run_raw_run(args: argparse.Namespace) -> int:
    started = time.time()
    db_path = resolve_db_path(args.db)
    schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
    raw_run = _inspect_raw_run(args.raw_run, db_path=db_path, known_endpoints=set(schemas))
    error_count = len(_raw_run_error_records(raw_run))
    if error_count:
        raise ConfigError(
            f"Raw run check failed for {error_count} file(s); "
            f"run `centric-api ingest check {args.raw_run}` for details."
        )
    progress: ProgressCallback | None = None if args.json else print

    _emit_progress(progress, "Ingest Raw Run")
    _emit_progress(progress, "")
    _emit_progress(progress, f"Raw:      {raw_run.path}")
    _emit_progress(progress, f"DB:       {db_path}")
    _emit_progress(progress, f"Mode:     {_manifest_mode(raw_run.manifest)}")
    _emit_progress(progress, f"Files:    {len(raw_run.files):,}")
    _emit_progress(progress, "")

    _emit_progress(progress, "ingest=running")
    ingest_started = time.time()
    ingest_result = ingest_raw_dir(raw_run.path, db_path, schemas=schemas)
    _emit_progress(
        progress,
        (
            f"ingest=ok applied_files={ingest_result.applied_files:,} "
            f"skipped_files={ingest_result.skipped_files:,} "
            f"records_read={ingest_result.records_read:,} "
            f"upserts={ingest_result.records_upserted:,} "
            f"deletes={ingest_result.records_deleted:,} "
            f"hard_deletes={ingest_result.records_hard_deleted:,} "
            f"invalid={ingest_result.invalid_records:,} "
            f"elapsed={format_duration(time.time() - ingest_started)}"
        ),
    )

    changelog_run: ChangelogRun | None = None
    changelog_skipped: str | None = None
    if args.changelog:
        _emit_progress(progress, "changelog=running")
        changelog_started = time.time()
        changelog_run, changelog_skipped = run_changelog_after_ingest(
            db_path,
            ingest_result,
            progress=_indented_progress(progress),
        )
        if changelog_skipped is not None:
            _emit_progress(
                progress,
                (
                    f"changelog=skipped reason={changelog_skipped} "
                    f"elapsed={format_duration(time.time() - changelog_started)}"
                ),
            )
        elif changelog_run is not None:
            _emit_progress(
                progress,
                (
                    f"changelog=ok events={changelog_run.event_count:,} "
                    f"scoped={changelog_run.scoped_record_count:,} "
                    f"elapsed={format_duration(time.time() - changelog_started)}"
                ),
            )

    payload = {
        "raw_run": _raw_run_record(raw_run),
        "db": str(db_path),
        "ingest": _ingest_record(ingest_result),
        "changelog": _changelog_record(changelog_run) if changelog_run is not None else None,
        "changelog_skipped": changelog_skipped,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    if args.json:
        print(json.dumps(payload, default=str))
    else:
        _emit_progress(progress, f"pipeline=done elapsed={format_duration(time.time() - started)}")
    return 0


def _inspect_raw_run(
    raw_run_arg: str,
    *,
    db_path: Path,
    known_endpoints: set[str],
) -> _RawRun:
    run_path = _resolve_raw_run_path(raw_run_arg)
    manifest = _load_manifest(run_path)
    manifest_sha256 = _sha256(run_path / "manifest.json")
    applied = _applied_raw_files(db_path)
    discovered_files = {item.path.resolve(): item for item in discover_raw_files(run_path)}
    files: list[_RawRunFile] = []
    for endpoint, endpoint_payload in _manifest_endpoints(manifest):
        filename = endpoint_payload.get("file")
        if not isinstance(filename, str) or not filename.strip():
            raise ConfigError(f"Manifest endpoint {endpoint!r} is missing a file name.")
        path = run_path / filename
        expected_count = _optional_int(endpoint_payload.get("items_fetched"))
        if expected_count is None:
            expected_count = _optional_int(endpoint_payload.get("expected_count"))
        discovered = discovered_files.get(path.resolve())
        is_delta = (
            bool(endpoint_payload.get("is_delta"))
            if discovered is None
            else discovered.is_delta
        )
        content_sha256: str | None = None
        line_count: int | None = None
        invalid_json_lines = 0
        if path.is_file():
            content_sha256 = _sha256(path)
            line_count, invalid_json_lines = _inspect_jsonl(path)
        applied_state = _applied_state(
            path=path,
            content_sha256=content_sha256,
            manifest_sha256=manifest_sha256,
            applied=applied,
        )
        files.append(
            _RawRunFile(
                endpoint=endpoint,
                path=path,
                expected_count=expected_count,
                is_delta=is_delta,
                content_sha256=content_sha256,
                line_count=line_count,
                invalid_json_lines=invalid_json_lines,
                known_schema=endpoint in known_endpoints,
                applied_state=applied_state,
            )
        )
    if not files:
        raise ConfigError(f"Raw run manifest has no endpoint files: {run_path}")
    return _RawRun(
        path=run_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        files=files,
    )


def _resolve_raw_run_path(value: str) -> Path:
    raw_value = value.strip()
    if not raw_value:
        raise ConfigError("Raw run must be a run id or path.")
    explicit_path = Path(raw_value).expanduser()
    if explicit_path.exists():
        if not explicit_path.is_dir():
            raise ConfigError(f"Raw run must be a directory: {explicit_path}")
        return explicit_path.resolve()
    run_path = runtime_path(Path("raw") / "runs" / raw_value)
    if run_path.is_dir():
        return run_path.resolve()
    if explicit_path.is_absolute() or len(explicit_path.parts) > 1:
        raise ConfigError(f"Raw run directory not found: {explicit_path}")
    raise ConfigError(f"Raw run not found under {runtime_path('raw/runs')}: {raw_value}")


def _load_manifest(run_path: Path) -> dict[str, Any]:
    manifest_path = run_path / "manifest.json"
    if not manifest_path.is_file():
        raise ConfigError(f"Raw run manifest not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Raw run manifest is invalid JSON: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Raw run manifest must be an object: {manifest_path}")
    return payload


def _manifest_endpoints(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, dict):
        raise ConfigError("Raw run manifest must contain an 'endpoints' object.")
    rows: list[tuple[str, dict[str, Any]]] = []
    for endpoint, payload in sorted(endpoints.items()):
        if not isinstance(payload, dict):
            raise ConfigError(f"Manifest endpoint {endpoint!r} must be an object.")
        rows.append((str(endpoint), payload))
    return rows


def _inspect_jsonl(path: Path) -> tuple[int, int]:
    line_count = 0
    invalid_json_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            line_count += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_json_lines += 1
                continue
            if not isinstance(payload, dict):
                invalid_json_lines += 1
    return line_count, invalid_json_lines


def _applied_raw_files(db_path: Path) -> dict[str, tuple[str, str | None]]:
    if not db_path.is_file():
        return {}
    try:
        with connect_readonly(db_path) as conn:
            if not table_exists(conn, "applied_raw_files"):
                return {}
            rows = conn.execute(
                """
                SELECT file_path, content_sha256, manifest_sha256
                FROM applied_raw_files
                """
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise ConfigError(f"Could not inspect applied raw files in DB: {db_path}") from exc
    return {
        str(row["file_path"]): (
            str(row["content_sha256"]),
            str(row["manifest_sha256"]) if row["manifest_sha256"] is not None else None,
        )
        for row in rows
    }


def _applied_state(
    *,
    path: Path,
    content_sha256: str | None,
    manifest_sha256: str,
    applied: dict[str, tuple[str, str | None]],
) -> str:
    if not path.is_file():
        return "missing_file"
    applied_hashes = applied.get(str(path))
    if applied_hashes is None:
        return "new"
    applied_content_sha256, applied_manifest_sha256 = applied_hashes
    if applied_content_sha256 != content_sha256:
        return "content_drift"
    if applied_manifest_sha256 != manifest_sha256:
        return "manifest_drift"
    return "applied"


def _check_payload(
    raw_run: _RawRun,
    *,
    db_path: Path,
    elapsed_seconds: float,
) -> dict[str, Any]:
    records = [_raw_run_file_record(file) for file in raw_run.files]
    errors = _raw_run_error_records(raw_run)
    warnings = [record for record in records if not record["known_schema"]]
    status = "failed" if errors else ("warn" if warnings else "ok")
    return {
        "status": status,
        "raw_run": _raw_run_record(raw_run),
        "db": str(db_path),
        "files": records,
        "errors": len(errors),
        "warnings": len(warnings),
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def _raw_run_error_records(raw_run: _RawRun) -> list[dict[str, Any]]:
    return [
        record
        for record in (_raw_run_file_record(file) for file in raw_run.files)
        if record["applied_state"] in {"missing_file", "content_drift", "manifest_drift"}
        or record["invalid_json_lines"] > 0
        or record["count_mismatch"]
    ]


def _raw_run_record(raw_run: _RawRun) -> dict[str, Any]:
    return {
        "path": str(raw_run.path),
        "run_id": raw_run.manifest.get("run_id"),
        "mode": _manifest_mode(raw_run.manifest),
        "manifest_sha256": raw_run.manifest_sha256,
        "file_count": len(raw_run.files),
        "endpoints": [file.endpoint for file in raw_run.files],
    }


def _raw_run_file_record(file: _RawRunFile) -> dict[str, Any]:
    return {
        "endpoint": file.endpoint,
        "path": str(file.path),
        "mode": "delta" if file.is_delta else "full",
        "expected_count": file.expected_count,
        "line_count": file.line_count,
        "count_mismatch": (
            file.expected_count is not None
            and file.line_count is not None
            and file.expected_count != file.line_count
        ),
        "invalid_json_lines": file.invalid_json_lines,
        "known_schema": file.known_schema,
        "applied_state": file.applied_state,
        "content_sha256": file.content_sha256,
    }


def _ingest_record(result: IngestResult) -> dict[str, Any]:
    return {
        "applied_files": result.applied_files,
        "skipped_files": result.skipped_files,
        "records_read": result.records_read,
        "records_upserted": result.records_upserted,
        "records_deleted": result.records_deleted,
        "records_hard_deleted": result.records_hard_deleted,
        "invalid_records": result.invalid_records,
        "endpoints": result.endpoints,
    }


def _changelog_record(run: ChangelogRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "endpoint_count": run.endpoint_count,
        "record_count": run.record_count,
        "event_count": run.event_count,
        "full_refresh": run.full_refresh,
        "scoped_record_count": run.scoped_record_count,
    }


def _print_check(raw_run: _RawRun, payload: dict[str, Any]) -> None:
    print("Ingest Raw Run Check")
    print()
    print(f"Status:   {payload['status']}")
    print(f"Raw:      {raw_run.path}")
    print(f"Run:      {raw_run.manifest.get('run_id') or raw_run.path.name}")
    print(f"Mode:     {_manifest_mode(raw_run.manifest)}")
    print(f"Files:    {len(raw_run.files):,}")
    print(f"DB:       {payload['db']}")
    print()
    print("Endpoint                 Mode   Records   Expected     Invalid   Schema  Applied")
    print("--------------------------------------------------------------------------------")
    for file in raw_run.files:
        records = "-" if file.line_count is None else f"{file.line_count:,}"
        expected = "-" if file.expected_count is None else f"{file.expected_count:,}"
        schema = "yes" if file.known_schema else "no"
        print(
            f"{file.endpoint:<24} "
            f"{('delta' if file.is_delta else 'full'):<6} "
            f"{records:>9} "
            f"{expected:>10} "
            f"{file.invalid_json_lines:>11,} "
            f"{schema:<7} "
            f"{file.applied_state}"
        )
    print()
    print(f"Elapsed:  {format_duration(payload['elapsed_seconds'])}")


def _emit_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _indented_progress(progress: ProgressCallback | None) -> ProgressCallback | None:
    if progress is None:
        return None

    def emit(message: str) -> None:
        progress(f"  {message}")

    return emit


def _manifest_mode(manifest: dict[str, Any]) -> str:
    mode = manifest.get("mode")
    return str(mode) if mode else "unknown"


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


__all__ = ["run_ingest_command"]
