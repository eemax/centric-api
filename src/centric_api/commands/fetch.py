from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from ..auth import AuthError, init_auth_context
from ..config import load_fetcher_settings, resolve_private_config_path, runtime_path
from ..defaults import (
    DEFAULT_DELTA_STATE_PATH,
    DEFAULT_FETCH_LOG_PATH,
    DEFAULT_LOCK_PATH,
    DEFAULT_OVERLAP_DAYS,
    DEFAULT_OVERLAP_MINUTES,
)
from ..fetch_delta_state import (
    derive_delta_floor,
    load_delta_state,
    normalize_int,
    update_delta_state_for_endpoint,
    write_delta_state,
)
from ..fetch_manifest import (
    endpoint_manifest_record,
    fetch_result_has_warning,
    fetch_result_status,
    write_run_manifest,
)
from ..fetcher import FetchError, run_endpoint
from ..models import FetchProgressEvent, FetchRunResult
from ..raw_lifecycle import (
    active_run_dir,
    completed_run_dir,
    failed_run_dir,
    promote_run_dir,
    write_running_marker,
)
from ..rendering.fetch import (
    print_delta_dry_run,
    print_human_fetch_run_header,
    print_human_fetch_summary,
    print_json_fetch_records,
    write_progress_line,
)
from ..rendering.logs import LogCallback, build_log_callback, format_duration
from ..schema import load_endpoint_schemas
from ..store import ingest_raw_dir
from ._fetch_pipeline import DeltaStateUpdate, PipelineProgressCallback, run_post_fetch_pipeline
from ._fetch_runtime import (
    allocate_run_id as _allocate_run_id,
)
from ._fetch_runtime import (
    delta_floor_reason as _delta_floor_reason,
)
from ._fetch_runtime import (
    endpoint_window_context as _endpoint_window_context,
)
from ._fetch_runtime import (
    prepare_runtime_spec as _prepare_runtime_spec,
)
from ._fetch_runtime import (
    remap_result_output_files as _remap_result_output_files,
)
from ._fetch_runtime import (
    resolve_fetch_mode as _resolve_fetch_mode,
)
from ._fetch_runtime import (
    select_endpoints as _select_endpoints,
)
from .common import release_fetch_lock, try_acquire_fetch_lock, utc_iso, utc_now
from .pipeline import run_changelog_after_ingest


def run_fetch(args: argparse.Namespace) -> int:
    if args.delta_dry_run:
        return _run_fetch_unlocked(args)
    if not getattr(args, "skip_fetch_lock", False):
        lock_file = runtime_path(DEFAULT_LOCK_PATH)
        lock_error = try_acquire_fetch_lock(lock_file)
        if lock_error is not None:
            print(f"Error: {lock_error}", file=sys.stderr)
            return 1
        try:
            return _run_fetch_unlocked(args)
        finally:
            release_fetch_lock(lock_file)
    return _run_fetch_unlocked(args)


def _run_fetch_unlocked(args: argparse.Namespace) -> int:
    started = time.time()
    run_started_dt = utc_now()
    mode, modified_since = _resolve_fetch_mode(args, run_started_dt)
    fetcher_cfg, auth_settings, endpoint_specs = load_fetcher_settings(args.fetch_config)

    selected_specs = _select_endpoints(endpoint_specs, args.endpoint)
    delta_state_file = resolve_private_config_path(DEFAULT_DELTA_STATE_PATH, args.delta_state_file)
    delta_state_exists = delta_state_file.is_file()
    delta_state = load_delta_state(delta_state_file)
    overlap_minutes = normalize_int(delta_state.get("overlap_minutes"), DEFAULT_OVERLAP_MINUTES)
    overlap_days = normalize_int(delta_state.get("overlap_days"), DEFAULT_OVERLAP_DAYS)
    delta_state["overlap_minutes"] = overlap_minutes
    delta_state["overlap_days"] = overlap_days

    if args.delta_dry_run:
        for spec in selected_specs:
            delta_floor = derive_delta_floor(
                delta_state,
                spec.name,
                overlap_minutes,
                overlap_days,
                utc_iso=utc_iso,
            )
            runtime_spec = _prepare_runtime_spec(
                spec,
                mode=mode,
                delta_floor=delta_floor,
                modified_since=modified_since,
            )
            print_delta_dry_run(
                runtime_spec,
                delta_floor=delta_floor,
                overlap_days=overlap_days,
                overlap_minutes=overlap_minutes,
            )
        return 0

    raw_root_dir = fetcher_cfg.output_dir
    run_id = _allocate_run_id(raw_root_dir, run_started_dt, mode, args.days or args.months)
    fetcher_cfg.output_dir = active_run_dir(raw_root_dir, run_id)
    write_running_marker(
        fetcher_cfg.output_dir,
        run_id=run_id,
        mode=mode,
        started_at=utc_iso(run_started_dt),
    )

    fetch_log_file: TextIO | None = None
    fetch_log_path: Path | None = None
    log_callback: LogCallback | None = None
    if args.log_level != "off":
        fetch_log_path = runtime_path(DEFAULT_FETCH_LOG_PATH)
        fetch_log_path.parent.mkdir(parents=True, exist_ok=True)
        fetch_log_file = fetch_log_path.open("a", encoding="utf-8")
        log_callback = build_log_callback(
            fetch_log_file,
            log_level=args.log_level,
            utc_iso=utc_iso,
        )
        log_callback(
            {
                "level": "summary",
                "event": "run_start",
                "run_id": run_id,
                "mode": mode,
                "endpoints": [spec.name for spec in selected_specs],
                "endpoint_count": len(selected_specs),
                "output_dir": str(fetcher_cfg.output_dir),
                "delta_state_file": str(delta_state_file),
                "modified_since": modified_since,
                "overlap_minutes": overlap_minutes if mode == "delta" else None,
            }
        )

    results: list[FetchRunResult] = []
    failures: list[tuple[str, str]] = []
    endpoint_records: list[dict[str, Any]] = []
    pending_successful_delta_updates: list[DeltaStateUpdate] = []
    pipeline_progress = None if args.quiet or args.json else _write_pipeline_progress
    fetch_progress = None if args.quiet else _fetch_progress_writer()
    if not args.quiet and not args.json:
        print_human_fetch_run_header(
            mode=mode,
            run_id=run_id,
            raw_dir=fetcher_cfg.output_dir,
            selected_count=len(selected_specs),
            delta_state_file=delta_state_file,
            overlap_days=overlap_days,
            overlap_minutes=overlap_minutes,
            modified_since=modified_since,
        )
    try:
        with init_auth_context(
            auth_settings,
            env_file=Path(args.env_file).expanduser() if args.env_file else None,
        ) as auth_ctx:
            fetcher_cfg.base_url = auth_ctx.base_url
            fetcher_cfg.timeout = auth_ctx.timeout
            for spec in selected_specs:
                attempt_start_dt = utc_now()
                attempt_start = utc_iso(attempt_start_dt)
                delta_floor = (
                    derive_delta_floor(
                        delta_state,
                        spec.name,
                        overlap_minutes,
                        overlap_days,
                        utc_iso=utc_iso,
                    )
                    if mode == "delta"
                    else None
                )
                delta_floor_reason = (
                    _delta_floor_reason(delta_state, spec.name, delta_state_exists)
                    if mode == "delta" and delta_floor is None
                    else None
                )
                runtime_spec = _prepare_runtime_spec(
                    spec,
                    mode=mode,
                    delta_floor=delta_floor,
                    modified_since=modified_since,
                )
                try:
                    if log_callback:
                        log_callback(
                            {
                                "level": "summary",
                                "event": "endpoint_start",
                                "endpoint": spec.name,
                                "mode": mode,
                                "delta_floor": delta_floor,
                                "delta_floor_reason": delta_floor_reason,
                            }
                        )
                    result = run_endpoint(
                        runtime_spec,
                        auth_ctx,
                        fetcher_cfg,
                        resume=args.resume,
                        append_output=mode == "delta",
                        output_file_suffix=".delta" if mode == "delta" else "",
                        create_empty_output=mode == "full",
                        delta_floor=delta_floor if mode == "delta" else None,
                        delta_floor_reason=delta_floor_reason,
                        modified_since=modified_since,
                        progress_callback=fetch_progress,
                        api_log_callback=log_callback,
                    )
                    results.append(result)
                    status = fetch_result_status(result, uppercase=True)
                    attempt_end = utc_iso()
                    if log_callback:
                        event = "endpoint_warn" if status == "WARN" else "endpoint_ok"
                        log_callback(
                            {
                                "level": "summary",
                                "event": event,
                                "endpoint": result.endpoint,
                                "mode": mode,
                                "status": status,
                                "expected": result.expected_count,
                                "fetched": result.items_fetched,
                                "pages": result.pages_fetched,
                                "retries": result.retries_used,
                                "warnings": len(result.warnings),
                                "duration_seconds": round(result.duration_seconds, 3),
                                "output": (
                                    str(result.output_file) if result.output_file_created else None
                                ),
                                "count_validation": result.count_validation_status,
                                "id_validation": result.id_validation_status,
                                "unique_ids": result.id_validation_unique_ids,
                            }
                        )
                    endpoint_records.append(
                        endpoint_manifest_record(
                            result,
                            mode=mode,
                            status=status,
                            attempt_start=attempt_start,
                            attempt_end=attempt_end,
                            delta_floor=delta_floor,
                            modified_since=modified_since,
                        )
                    )
                    if mode in {"delta", "full"}:
                        pending_successful_delta_updates.append(
                            DeltaStateUpdate(
                                endpoint_name=spec.name,
                                status=status,
                                attempt_start=attempt_start,
                                attempt_end=attempt_end,
                            )
                        )
                except (AuthError, FetchError) as exc:
                    message = str(exc)
                    failures.append((spec.name, message))
                    attempt_duration = (datetime.now(UTC) - attempt_start_dt).total_seconds()
                    error_pieces = [f"elapsed={format_duration(attempt_duration)}"]
                    window_context = _endpoint_window_context(
                        mode=mode,
                        delta_floor=delta_floor,
                        delta_floor_reason=delta_floor_reason,
                        modified_since=modified_since,
                    )
                    if window_context is not None:
                        error_pieces.append(window_context)
                    error_pieces.append(message)
                    print(f"[{spec.name}] ERROR  {'  '.join(error_pieces)}", file=sys.stderr)
                    attempt_end = utc_iso()
                    if log_callback:
                        log_callback(
                            {
                                "level": "summary",
                                "event": "endpoint_failed",
                                "endpoint": spec.name,
                                "mode": mode,
                                "duration_seconds": round(attempt_duration, 3),
                                "error": message,
                            }
                        )
                    endpoint_records.append(
                        {
                            "endpoint": spec.name,
                            "mode": mode,
                            "status": "FAILED",
                            "attempt_start": attempt_start,
                            "attempt_end": attempt_end,
                            "delta_floor": delta_floor,
                            "modified_since": modified_since,
                            "error": message,
                        }
                    )
                    if mode in {"delta", "full"}:
                        update_delta_state_for_endpoint(
                            delta_state,
                            endpoint_name=spec.name,
                            status="FAILED",
                            attempt_start=attempt_start,
                            attempt_end=attempt_end,
                            error=message,
                        )
                        write_delta_state(delta_state_file, delta_state)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt) and log_callback:
            endpoints_warn = sum(1 for result in results if fetch_result_has_warning(result))
            log_callback(
                {
                    "level": "summary",
                    "event": "run_interrupted",
                    "run_id": run_id,
                    "mode": mode,
                    "endpoints_ok": len(results) - endpoints_warn,
                    "endpoints_warn": endpoints_warn,
                    "endpoints_failed": len(failures),
                    "endpoints_total": len(selected_specs),
                    "duration_seconds": round(time.time() - started, 3),
                }
            )
        if fetch_log_file is not None:
            fetch_log_file.close()
            fetch_log_file = None
        raise

    raw_run_failed = bool(failures)
    if raw_run_failed:
        fetcher_cfg.output_dir = promote_run_dir(
            fetcher_cfg.output_dir,
            failed_run_dir(raw_root_dir, run_id),
        )
    else:
        fetcher_cfg.output_dir = promote_run_dir(
            fetcher_cfg.output_dir,
            completed_run_dir(raw_root_dir, run_id),
        )
    _remap_result_output_files(results, fetcher_cfg.output_dir)

    pipeline_result = run_post_fetch_pipeline(
        db_arg=args.db,
        schema_arg=args.schema,
        fetcher_cfg=fetcher_cfg,
        raw_root_dir=raw_root_dir,
        run_id=run_id,
        mode=mode,
        run_started_dt=run_started_dt,
        selected_specs=selected_specs,
        results=results,
        failures=failures,
        endpoint_records=endpoint_records,
        modified_since=modified_since,
        pipeline_progress=pipeline_progress,
        log_callback=log_callback,
        delta_state=delta_state,
        delta_state_file=delta_state_file,
        pending_successful_delta_updates=pending_successful_delta_updates,
        raw_run_failed=raw_run_failed,
        write_run_manifest_func=write_run_manifest,
        load_endpoint_schemas_func=load_endpoint_schemas,
        ingest_raw_dir_func=ingest_raw_dir,
        run_changelog_after_ingest_func=run_changelog_after_ingest,
    )
    manifest_path = pipeline_result.manifest_path
    ingest_result = pipeline_result.ingest_result
    changelog_run = pipeline_result.changelog_run
    changelog_skipped = pipeline_result.changelog_skipped
    pipeline_error = pipeline_result.pipeline_error
    duration_seconds = time.time() - started
    endpoints_warn = sum(1 for result in results if fetch_result_has_warning(result))
    endpoints_ok = len(results) - endpoints_warn
    run_status = "ok"
    if pipeline_error or (selected_specs and len(failures) == len(selected_specs)):
        run_status = "failed"
    elif failures:
        run_status = "partial"
    elif endpoints_warn:
        run_status = "warn"
    _emit_run_result_progress(
        pipeline_progress,
        status=run_status,
        endpoints_ok=endpoints_ok,
        endpoints_warn=endpoints_warn,
        endpoints_failed=len(failures),
        endpoints_total=len(selected_specs),
        records=sum(result.items_fetched for result in results),
        pages=sum(result.pages_fetched for result in results),
        retries=sum(result.retries_used for result in results),
        elapsed_seconds=duration_seconds,
    )
    if log_callback:
        log_callback(
            {
                "level": "summary",
                "event": f"run_{run_status}",
                "run_id": run_id,
                "mode": mode,
                "endpoints_ok": endpoints_ok,
                "endpoints_warn": endpoints_warn,
                "endpoints_failed": len(failures),
                "endpoints_total": len(selected_specs),
                "fetched": sum(result.items_fetched for result in results),
                "pages": sum(result.pages_fetched for result in results),
                "retries": sum(result.retries_used for result in results),
                "duration_seconds": round(duration_seconds, 3),
                "manifest": str(manifest_path),
                "pipeline_error": pipeline_error,
            }
        )
    if fetch_log_file is not None:
        fetch_log_file.close()
    if args.json:
        print_json_fetch_records(
            results,
            failures,
            manifest_path=manifest_path,
            ingest_result=ingest_result,
            changelog_run=changelog_run,
            changelog_skipped=changelog_skipped,
            pipeline_error=pipeline_error,
        )
    elif not args.quiet:
        print_human_fetch_summary(
            mode=mode,
            run_id=run_id,
            raw_dir=fetcher_cfg.output_dir,
            selected_count=len(selected_specs),
            results=results,
            failures=failures,
            duration_seconds=duration_seconds,
            ingest_result=ingest_result,
            changelog_run=changelog_run,
            changelog_skipped=changelog_skipped,
            pipeline_error=pipeline_error,
            log_path=fetch_log_path if args.log_level != "off" else None,
        )
    return 1 if failures or pipeline_error else 0


def _write_pipeline_progress(message: str) -> None:
    print(message, file=sys.stderr)


def _fetch_progress_writer() -> Callable[[FetchProgressEvent], None]:
    seen_endpoint = False

    def emit(event: FetchProgressEvent) -> None:
        nonlocal seen_endpoint
        if event.kind == "endpoint_start":
            if seen_endpoint:
                print(file=sys.stderr)
            seen_endpoint = True
        write_progress_line(event)

    return emit


def _emit_run_result_progress(
    progress: PipelineProgressCallback | None,
    *,
    status: str,
    endpoints_ok: int,
    endpoints_warn: int,
    endpoints_failed: int,
    endpoints_total: int,
    records: int,
    pages: int,
    retries: int,
    elapsed_seconds: float,
) -> None:
    if progress is None:
        return
    progress("")
    progress("Fetch result")
    progress(
        f"status={status} endpoints={_fmt_cli_int(endpoints_ok)} ok, "
        f"{_fmt_cli_int(endpoints_warn)} warn, {_fmt_cli_int(endpoints_failed)} failed, "
        f"{_fmt_cli_int(endpoints_total)} total "
        f"records={_fmt_cli_int(records)} pages={_fmt_cli_int(pages)} "
        f"retries={_fmt_cli_int(retries)} elapsed={format_duration(elapsed_seconds)}"
    )


def _fmt_cli_int(value: int) -> str:
    return f"{value:,}"
