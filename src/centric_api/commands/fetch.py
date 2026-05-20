from __future__ import annotations

import argparse
import calendar
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from ..auth import AuthError, init_auth_context
from ..changelog import ChangelogRun, record_changelog
from ..cli_output import (
    LogCallback,
    _build_log_callback,
    _print_delta_dry_run,
    _print_human_fetch_summary,
    _print_json_fetch_records,
    _write_progress_line,
)
from ..config import ConfigError, load_fetcher_settings, resolve_private_config_path, runtime_path
from ..defaults import (
    DEFAULT_DELTA_STATE_PATH,
    DEFAULT_FETCH_LOG_PATH,
    DEFAULT_LOCK_PATH,
    DEFAULT_OVERLAP_DAYS,
    DEFAULT_OVERLAP_MINUTES,
)
from ..defaults import db_path as resolve_db_path
from ..delta import apply_data_sort, strip_modified_at_filters
from ..fetch_delta_state import (
    derive_delta_floor,
    load_delta_state,
    normalize_int,
    update_delta_state_for_endpoint,
    write_delta_state,
)
from ..fetch_manifest import endpoint_manifest_record, write_run_manifest
from ..fetcher import FetchError, run_endpoint
from ..models import EndpointSpec, FetchRunResult
from ..schema import load_endpoint_schemas
from ..store import IngestResult, ingest_raw_dir
from .common import release_fetch_lock, try_acquire_fetch_lock, utc_iso, utc_now


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

    run_id = _run_id(run_started_dt, mode, args.days or args.months)
    fetcher_cfg.output_dir = fetcher_cfg.output_dir / "runs" / run_id
    selected_specs = _select_endpoints(endpoint_specs, args.endpoint)
    delta_state_file = resolve_private_config_path(DEFAULT_DELTA_STATE_PATH, args.delta_state_file)
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
            _print_delta_dry_run(
                runtime_spec,
                delta_floor=delta_floor,
                overlap_days=overlap_days,
                overlap_minutes=overlap_minutes,
            )
        return 0

    fetch_log_file: TextIO | None = None
    log_callback: LogCallback | None = None
    if args.log_level != "off":
        log_path = runtime_path(DEFAULT_FETCH_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fetch_log_file = log_path.open("a", encoding="utf-8")
        log_callback = _build_log_callback(
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
                        progress_callback=None if args.quiet else _write_progress_line,
                        api_log_callback=log_callback,
                    )
                    results.append(result)
                    status = "OK"
                    attempt_end = utc_iso()
                    if log_callback:
                        log_callback(
                            {
                                "level": "summary",
                                "event": "endpoint_ok",
                                "endpoint": result.endpoint,
                                "mode": mode,
                                "expected": result.expected_count,
                                "fetched": result.items_fetched,
                                "pages": result.pages_fetched,
                                "retries": result.retries_used,
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
                        update_delta_state_for_endpoint(
                            delta_state,
                            endpoint_name=spec.name,
                            status=status,
                            attempt_start=attempt_start,
                            attempt_end=attempt_end,
                            error=None,
                        )
                        write_delta_state(delta_state_file, delta_state)
                except (AuthError, FetchError) as exc:
                    message = str(exc)
                    failures.append((spec.name, message))
                    print(f"[{spec.name}] error: {message}", file=sys.stderr)
                    attempt_end = utc_iso()
                    if log_callback:
                        log_callback(
                            {
                                "level": "summary",
                                "event": "endpoint_failed",
                                "endpoint": spec.name,
                                "mode": mode,
                                "duration_seconds": round(
                                    (datetime.now(UTC) - attempt_start_dt).total_seconds(),
                                    3,
                                ),
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
    except Exception:
        if fetch_log_file is not None:
            fetch_log_file.close()
        raise

    manifest_path = write_run_manifest(
        output_dir=fetcher_cfg.output_dir,
        run_id=run_id,
        mode=mode,
        run_started_at=run_started_dt,
        run_finished_at=utc_now(),
        selected_specs=selected_specs,
        results=results,
        failures=failures,
        endpoint_records=endpoint_records,
        modified_since=modified_since,
        utc_iso=utc_iso,
    )
    ingest_result: IngestResult | None = None
    changelog_run: ChangelogRun | None = None
    changelog_skipped: str | None = None
    pipeline_error: str | None = None
    if results:
        db_path = resolve_db_path(args.db)
        schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
        ingest_result = ingest_raw_dir(fetcher_cfg.output_dir, db_path, schemas=schemas)
        if log_callback:
            log_callback(
                {
                    "level": "summary",
                    "event": "ingest_ok",
                    "applied_files": ingest_result.applied_files,
                    "skipped_files": ingest_result.skipped_files,
                    "records_read": ingest_result.records_read,
                    "upserts": ingest_result.records_upserted,
                    "deletes": ingest_result.records_deleted,
                    "hard_deletes": ingest_result.records_hard_deleted,
                    "invalid": ingest_result.invalid_records,
                }
            )
        try:
            changelog_run, changelog_skipped = _run_changelog_after_ingest(db_path, ingest_result)
        except Exception as exc:
            pipeline_error = f"changelog failed after ingest: {exc}"
            if log_callback:
                log_callback(
                    {
                        "level": "summary",
                        "event": "changelog_failed",
                        "error": str(exc),
                    }
                )
        else:
            if log_callback:
                if changelog_run is not None:
                    log_callback(
                        {
                            "level": "summary",
                            "event": "changelog_ok",
                            "events": changelog_run.event_count,
                            "scoped": changelog_run.scoped_record_count,
                            "run_id": changelog_run.run_id,
                        }
                    )
                elif changelog_skipped:
                    log_callback(
                        {
                            "level": "summary",
                            "event": "changelog_skipped",
                            "reason": changelog_skipped,
                        }
                    )
    elif log_callback:
        log_callback(
            {
                "level": "summary",
                "event": "ingest_skipped",
                "reason": "no successful endpoint fetches",
            }
        )

    duration_seconds = time.time() - started
    if log_callback:
        run_status = "ok"
        if pipeline_error or (selected_specs and len(failures) == len(selected_specs)):
            run_status = "failed"
        elif failures:
            run_status = "partial"
        log_callback(
            {
                "level": "summary",
                "event": f"run_{run_status}",
                "run_id": run_id,
                "mode": mode,
                "endpoints_ok": len(results),
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
        _print_json_fetch_records(
            results,
            failures,
            manifest_path=manifest_path,
            ingest_result=ingest_result,
            changelog_run=changelog_run,
            changelog_skipped=changelog_skipped,
            pipeline_error=pipeline_error,
        )
    elif not args.quiet:
        _print_human_fetch_summary(
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
        )
    return 1 if failures or pipeline_error else 0


def _resolve_fetch_mode(args: argparse.Namespace, now: datetime) -> tuple[str, str | None]:
    if args.full and (args.days is not None or args.months is not None):
        raise ConfigError("Use --full, --days, or --months separately.")
    if args.days is not None and args.months is not None:
        raise ConfigError("Use either --days or --months, not both.")
    if args.delta_dry_run:
        return "delta", None
    if args.full:
        return "full", None
    if args.days is not None:
        return "days", utc_iso(now - timedelta(days=args.days))
    if args.months is not None:
        return "months", utc_iso(_subtract_calendar_months(now, args.months))
    return "delta", None


def _prepare_runtime_spec(
    spec: EndpointSpec,
    *,
    mode: str,
    delta_floor: str | None,
    modified_since: str | None,
) -> EndpointSpec:
    runtime_spec = apply_data_sort(spec, sort_value="_modified_at", policy="force")
    if mode == "delta" and delta_floor is not None:
        return _apply_modified_since_filter(runtime_spec, delta_floor)
    if mode in {"days", "months"} and modified_since is not None:
        return _apply_modified_since_filter(runtime_spec, modified_since)
    return runtime_spec


def _apply_modified_since_filter(spec: EndpointSpec, modified_since: str) -> EndpointSpec:
    query_params = strip_modified_at_filters(spec.query_params)
    query_params["_modified_at=ge"] = modified_since
    count_query_params = strip_modified_at_filters(spec.count_spec.query_params)
    count_query_params["_modified_at=ge"] = modified_since
    next_count_spec = replace(spec.count_spec, query_params=count_query_params)
    return replace(spec, query_params=query_params, count_spec=next_count_spec)


def _run_changelog_after_ingest(
    db_path: Path,
    ingest_result: IngestResult,
) -> tuple[ChangelogRun | None, str | None]:
    changed_endpoints = set(ingest_result.changed_record_ids_by_endpoint)
    if not changed_endpoints:
        return None, "no current-record changes"
    return (
        record_changelog(
            db_path,
            endpoints=changed_endpoints,
            record_ids_by_endpoint={
                endpoint: set(record_ids)
                for endpoint, record_ids in ingest_result.upserted_record_ids_by_endpoint.items()
            },
            deleted_record_ids_by_endpoint={
                endpoint: set(record_ids)
                for endpoint, record_ids in ingest_result.deleted_record_ids_by_endpoint.items()
            },
            deleted_record_delete_types_by_endpoint=ingest_result.deleted_record_delete_types_by_endpoint,
        ),
        None,
    )


def _select_endpoints(all_specs: list[EndpointSpec], names: list[str]) -> list[EndpointSpec]:
    if not names:
        return all_specs
    wanted = set(names)
    selected = [spec for spec in all_specs if spec.name in wanted]
    missing = sorted(wanted - {spec.name for spec in selected})
    if missing:
        raise ConfigError(f"Unknown endpoint names: {', '.join(missing)}")
    return selected


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    total_month_index = (value.year * 12 + (value.month - 1)) - months
    year = total_month_index // 12
    month = (total_month_index % 12) + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _run_id(value: datetime, mode: str, amount: int | None) -> str:
    base = value.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    if mode in {"days", "months"} and amount is not None:
        return f"{base}-{mode}{amount}"
    return f"{base}-{mode}"
