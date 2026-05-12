from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from croniter import croniter

from .auth import AuthError, init_auth_context
from .changelog import (
    ChangelogRun,
    list_change_summary,
    list_changelog_runs,
    list_changes,
    list_field_summary,
    parse_since,
    record_changelog,
)
from .config import ConfigError, load_fetcher_settings, resolve_private_config_path, runtime_path
from .delta import apply_data_sort, strip_modified_at_filters
from .fetcher import FetchError, run_endpoint
from .models import EndpointSpec, FetchProgressEvent, FetchRunResult
from .schema import load_endpoint_schemas
from .store import IngestResult, ingest_raw_dir

DEFAULT_CONFIG_PATH = Path("config/fetcher.yml")
DEFAULT_DELTA_STATE_PATH = Path("delta.yml")
DEFAULT_FETCH_LOG_PATH = Path("logs/fetch.log")
DEFAULT_DELTA_LOG_PATH = Path("logs/delta.log")
DEFAULT_DB_PATH = Path("centric.db")
DEFAULT_LOCK_PATH = Path("cron/fetch.lock")
DEFAULT_CRON_LOG_PATH = Path("logs/cron.log")
DEFAULT_OVERLAP_MINUTES = 60
DEFAULT_OVERLAP_DAYS = 0
MIN_DAYS_BACK = 1
MAX_DAYS_BACK = 3650
MIN_MONTHS_BACK = 1
MAX_MONTHS_BACK = 120
SENSITIVE_QUERY_KEYS = {"token", "password", "api_key", "authorization"}
LOG_LEVEL_RANKS = {"off": 0, "summary": 1, "http": 2, "debug": 3}

LogLevel = Literal["off", "summary", "http", "debug"]
LogEvent = dict[str, Any]
LogCallback = Callable[[LogEvent], None]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            return run_fetch(args)
        if args.command == "changelog":
            return run_changelog(args)
        if args.command == "cron":
            return run_cron(args)
    except (AuthError, ConfigError, FetchError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="centric-api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch Centric API records")
    fetch_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    fetch_parser.add_argument("--endpoint", action="append", default=[])
    fetch_parser.add_argument("--full", action="store_true", help="Refetch all records.")
    fetch_parser.add_argument("--days", type=_parse_days_back, default=None)
    fetch_parser.add_argument("--months", type=_parse_months_back, default=None)
    fetch_parser.add_argument("--resume", action="store_true")
    fetch_parser.add_argument("--db", default=None)
    fetch_parser.add_argument("--schema", default=None)
    fetch_parser.add_argument("--delta-state-file", default=None)
    fetch_parser.add_argument("--delta-dry-run", action="store_true")
    fetch_parser.add_argument("--env-file", default=None)
    fetch_parser.add_argument("--timeout", type=float, default=None)
    fetch_parser.add_argument("--quiet", action="store_true")
    fetch_parser.add_argument("--json", action="store_true")
    fetch_parser.add_argument("--log-level", choices=list(LOG_LEVEL_RANKS), default="off")

    changelog_parser = subparsers.add_parser("changelog", help="Inspect or update changelog")
    changelog_parser.add_argument(
        "action",
        nargs="?",
        choices=["summary", "fields", "runs", "changes", "update"],
        default="summary",
    )
    changelog_parser.add_argument("--db", default=None)
    changelog_parser.add_argument("--endpoint", action="append", default=[])
    changelog_parser.add_argument("--since", default=None)
    changelog_parser.add_argument("--limit", type=int, default=50)
    changelog_parser.add_argument("--json", action="store_true")

    cron_parser = subparsers.add_parser("cron", help="Run scheduled delta fetches in foreground")
    cron_parser.add_argument("schedule", nargs="?", default="0 * * * *")
    cron_parser.add_argument("--run-now", action="store_true")
    cron_parser.add_argument("--endpoint", action="append", default=[])
    cron_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    cron_parser.add_argument("--db", default=None)
    cron_parser.add_argument("--schema", default=None)
    cron_parser.add_argument("--delta-state-file", default=None)
    cron_parser.add_argument("--env-file", default=None)
    cron_parser.add_argument("--timeout", type=float, default=None)
    cron_parser.add_argument("--log-level", choices=list(LOG_LEVEL_RANKS), default="summary")
    return parser


def run_fetch(args: argparse.Namespace) -> int:
    started = time.time()
    run_started_dt = _utc_now()
    mode, modified_since = _resolve_fetch_mode(args, run_started_dt)
    fetcher_cfg, auth_settings, endpoint_specs = load_fetcher_settings(args.config)

    run_id = _run_id(run_started_dt, mode, args.days or args.months)
    fetcher_cfg.output_dir = fetcher_cfg.output_dir / "runs" / run_id
    selected_specs = _select_endpoints(endpoint_specs, args.endpoint)
    delta_state_file = resolve_private_config_path(DEFAULT_DELTA_STATE_PATH, args.delta_state_file)
    delta_log_file = runtime_path(DEFAULT_DELTA_LOG_PATH)
    delta_state = _load_delta_state(delta_state_file)
    overlap_minutes = _normalize_int(delta_state.get("overlap_minutes"), DEFAULT_OVERLAP_MINUTES)
    overlap_days = _normalize_int(delta_state.get("overlap_days"), DEFAULT_OVERLAP_DAYS)
    delta_state["overlap_minutes"] = overlap_minutes
    delta_state["overlap_days"] = overlap_days

    fetch_log_file: TextIO | None = None
    log_callback: LogCallback | None = None
    if args.log_level != "off":
        log_path = runtime_path(DEFAULT_FETCH_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fetch_log_file = log_path.open("a", encoding="utf-8")
        log_callback = _build_log_callback(fetch_log_file, log_level=args.log_level)

    results: list[FetchRunResult] = []
    failures: list[tuple[str, str]] = []
    endpoint_records: list[dict[str, Any]] = []
    try:
        with init_auth_context(
            auth_settings,
            timeout=args.timeout,
            env_file=Path(args.env_file).expanduser() if args.env_file else None,
        ) as auth_ctx:
            fetcher_cfg.base_url = auth_ctx.base_url
            fetcher_cfg.timeout = auth_ctx.timeout
            for spec in selected_specs:
                attempt_start_dt = _utc_now()
                attempt_start = _utc_iso(attempt_start_dt)
                delta_floor = (
                    _derive_delta_floor(delta_state, spec.name, overlap_minutes, overlap_days)
                    if mode == "delta"
                    else None
                )
                runtime_spec = _prepare_runtime_spec(
                    spec,
                    mode=mode,
                    delta_floor=delta_floor,
                    modified_since=modified_since,
                )
                if args.delta_dry_run:
                    _print_delta_dry_run(
                        runtime_spec,
                        delta_floor=delta_floor,
                        overlap_days=overlap_days,
                        overlap_minutes=overlap_minutes,
                    )
                    continue
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
                        delta_floor=delta_floor if mode == "delta" else None,
                        progress_callback=None if args.quiet else _write_progress_line,
                        api_log_callback=log_callback,
                    )
                    results.append(result)
                    status = _classify_status(result, None)
                    attempt_end = _utc_iso()
                    endpoint_records.append(
                        _endpoint_manifest_record(
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
                        _update_delta_state_for_endpoint(
                            delta_state,
                            endpoint_name=spec.name,
                            status=status,
                            attempt_start=attempt_start,
                            attempt_end=attempt_end,
                            error=None,
                        )
                        _write_delta_state(delta_state_file, delta_state)
                        _append_human_record(
                            delta_log_file,
                            {
                                "level": "summary",
                                "event": "delta_endpoint",
                                "run_at": attempt_end,
                                "mode": mode,
                                "endpoint": spec.name,
                                "status": status,
                                "delta_floor": delta_floor,
                                "items_fetched": result.items_fetched,
                                "pages_fetched": result.pages_fetched,
                                "retries_used": result.retries_used,
                                "output_file": str(result.output_file),
                            },
                        )
                except (AuthError, FetchError) as exc:
                    message = str(exc)
                    failures.append((spec.name, message))
                    print(f"[{spec.name}] error: {message}", file=sys.stderr)
                    attempt_end = _utc_iso()
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
                    if mode == "delta":
                        _update_delta_state_for_endpoint(
                            delta_state,
                            endpoint_name=spec.name,
                            status="FAILED",
                            attempt_start=attempt_start,
                            attempt_end=attempt_end,
                            error=message,
                        )
                        _write_delta_state(delta_state_file, delta_state)
    finally:
        if fetch_log_file is not None:
            fetch_log_file.close()

    if args.delta_dry_run:
        return 0

    manifest_path = _write_run_manifest(
        output_dir=fetcher_cfg.output_dir,
        run_id=run_id,
        mode=mode,
        run_started_at=run_started_dt,
        run_finished_at=_utc_now(),
        selected_specs=selected_specs,
        results=results,
        failures=failures,
        endpoint_records=endpoint_records,
        modified_since=modified_since,
    )
    ingest_result: IngestResult | None = None
    changelog_run: ChangelogRun | None = None
    changelog_skipped: str | None = None
    pipeline_error: str | None = None
    if results:
        db_path = _db_path(args.db)
        schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
        ingest_result = ingest_raw_dir(fetcher_cfg.output_dir, db_path, schemas=schemas)
        try:
            changelog_run, changelog_skipped = _run_changelog_after_ingest(db_path, ingest_result)
        except Exception as exc:
            pipeline_error = f"changelog failed after ingest: {exc}"

    duration_seconds = time.time() - started
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


def run_changelog(args: argparse.Namespace) -> int:
    db_path = _db_path(args.db)
    since = parse_since(args.since)
    if args.action == "update":
        run = record_changelog(
            db_path,
            endpoints=set(args.endpoint) if args.endpoint else None,
            full=True,
        )
        _print_or_json(
            args.json,
            {
                "run_id": run.run_id,
                "endpoint_count": run.endpoint_count,
                "record_count": run.record_count,
                "event_count": run.event_count,
                "full_refresh": run.full_refresh,
            },
            (
                f"Changelog updated: {run.record_count} records tracked across "
                f"{run.endpoint_count} endpoints, {run.event_count} events. Run: {run.run_id}"
            ),
        )
        return 0
    if args.action == "runs":
        rows = list_changelog_runs(db_path, since=since, limit=args.limit)
        return _print_rows(rows, args.json, empty_message="No changelog runs found.")
    if args.action == "changes":
        rows = list_changes(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=args.limit,
        )
        return _print_rows(rows, args.json, empty_message="No changelog changes found.")
    if args.action == "fields":
        rows = list_field_summary(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=args.limit,
        )
        return _print_rows(rows, args.json, empty_message="No changelog field changes found.")
    rows = list_change_summary(db_path, since=since, limit=args.limit)
    return _print_rows(rows, args.json, empty_message="No changelog events found.")


def run_cron(args: argparse.Namespace) -> int:
    schedule = args.schedule.strip()
    if len(schedule.split()) != 5 or not croniter.is_valid(schedule):
        raise ConfigError(f"Invalid cron schedule: {schedule!r}")
    lock_file = runtime_path(DEFAULT_LOCK_PATH)
    log_file = runtime_path(DEFAULT_CRON_LOG_PATH)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    print("Centric API cron starting")
    print(f"Schedule: {schedule}")
    print(f"Lock:     {lock_file}")
    print(f"Log:      {log_file}")
    _append_human_log(log_file, f"cron_start schedule={json.dumps(schedule)}")

    try:
        if args.run_now:
            _run_cron_fetch_once(args, lock_file=lock_file, log_file=log_file)
        while True:
            next_run = croniter(schedule, datetime.now().astimezone()).get_next(datetime)
            wait_seconds = max(0.0, (next_run - datetime.now().astimezone()).total_seconds())
            print(f"Next fetch: {next_run.astimezone().isoformat(timespec='seconds')}")
            time.sleep(wait_seconds)
            _run_cron_fetch_once(args, lock_file=lock_file, log_file=log_file)
    except KeyboardInterrupt:
        print("Cron stopped.")
        _append_human_log(log_file, "cron_stop")
        return 0


def _run_cron_fetch_once(args: argparse.Namespace, *, lock_file: Path, log_file: Path) -> None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.exists():
        print(f"Skipping fetch; lock exists: {lock_file}")
        _append_human_log(log_file, f"fetch_skipped_lock lock_file={json.dumps(str(lock_file))}")
        return
    lock_file.write_text(str(time.time()), encoding="utf-8")
    started = time.time()
    print(f"Fetch starting: {_utc_iso()}")
    try:
        fetch_args = argparse.Namespace(
            command="fetch",
            config=args.config,
            endpoint=args.endpoint,
            full=False,
            days=None,
            months=None,
            resume=False,
            db=args.db,
            schema=args.schema,
            delta_state_file=args.delta_state_file,
            delta_dry_run=False,
            env_file=args.env_file,
            timeout=args.timeout,
            quiet=False,
            json=False,
            log_level=args.log_level,
        )
        exit_code = run_fetch(fetch_args)
        duration = time.time() - started
        print(f"Fetch finished: exit={exit_code} duration={_format_duration(duration)}")
        _append_human_log(
            log_file,
            (
                "fetch_finish "
                f"exit_code={exit_code} duration_seconds={round(duration, 3)}"
            ),
        )
    finally:
        lock_file.unlink(missing_ok=True)


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
        return "days", _utc_iso(now - timedelta(days=args.days))
    if args.months is not None:
        return "months", _utc_iso(_subtract_calendar_months(now, args.months))
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
    next_count_spec = None
    if spec.count_spec is not None:
        count_query_params = strip_modified_at_filters(spec.count_spec.query_params)
        count_query_params["_modified_at=ge"] = modified_since
        next_count_spec = replace(spec.count_spec, query_params=count_query_params)
    return replace(spec, query_params=query_params, count_spec=next_count_spec)


def _derive_delta_floor(
    delta_state: dict[str, Any],
    endpoint_name: str,
    overlap_minutes: int,
    overlap_days: int,
) -> str | None:
    endpoint_state = delta_state.get("endpoints", {}).get(endpoint_name, {})
    if not isinstance(endpoint_state, dict):
        return None
    started_at = _parse_utc_iso(endpoint_state.get("last_successful_fetch_start"))
    if started_at is None:
        return None
    return _utc_iso(started_at - timedelta(minutes=overlap_minutes, days=overlap_days))


def _update_delta_state_for_endpoint(
    delta_state: dict[str, Any],
    *,
    endpoint_name: str,
    status: str,
    attempt_start: str,
    attempt_end: str,
    error: str | None,
) -> None:
    endpoints = delta_state.setdefault("endpoints", {})
    if not isinstance(endpoints, dict):
        endpoints = {}
        delta_state["endpoints"] = endpoints
    existing = endpoints.get(endpoint_name, {})
    if not isinstance(existing, dict):
        existing = {}
    existing["last_attempted_fetch_start"] = attempt_start
    existing["last_attempted_fetch_end"] = attempt_end
    existing["last_attempted_status"] = status
    existing["last_attempted_error"] = error
    if status in {"OK", "PARTIAL"}:
        existing["last_successful_fetch_start"] = attempt_start
        existing["last_successful_fetch_end"] = attempt_end
    endpoints[endpoint_name] = existing
    delta_state["version"] = 1
    delta_state["updated_at"] = attempt_end


def _load_delta_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "version": 1,
            "updated_at": None,
            "overlap_minutes": DEFAULT_OVERLAP_MINUTES,
            "overlap_days": DEFAULT_OVERLAP_DAYS,
            "endpoints": {},
        }
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Delta state root must be an object: {path}")
    endpoints = payload.get("endpoints", {})
    if not isinstance(endpoints, dict):
        raise ConfigError(f"Delta state endpoints must be an object: {path}")
    return {
        "version": 1,
        "updated_at": payload.get("updated_at"),
        "overlap_minutes": _normalize_int(payload.get("overlap_minutes"), DEFAULT_OVERLAP_MINUTES),
        "overlap_days": _normalize_int(payload.get("overlap_days"), DEFAULT_OVERLAP_DAYS),
        "endpoints": endpoints,
    }


def _write_delta_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.tmp"
    temp_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    temp_path.replace(path)


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
        ),
        None,
    )


def _write_progress_line(event: FetchProgressEvent) -> None:
    if event.kind == "endpoint_start":
        expected = event.expected_count if event.expected_count is not None else "unknown"
        print(
            f"[{event.endpoint}] start: skip={event.start_skip} limit={event.limit} "
            f"expected={expected} retries={event.retries_used} "
            f"elapsed={_format_seconds(event.elapsed_seconds)}",
            file=sys.stderr,
        )
        return
    if event.kind == "page_fetched":
        page_label = str(event.page_index)
        if event.expected_pages is not None:
            page_label = f"{page_label}/{event.expected_pages}"
        line = (
            f"[{event.endpoint}] page {page_label}: page_items={event.page_items} "
            f"total_items={event.items_fetched} skip={event.skip} next_skip={event.next_skip} "
            f"elapsed={_format_seconds(event.elapsed_seconds)}"
        )
        if event.percent_complete is not None:
            line += f" progress={event.percent_complete:.1f}%"
        if event.rolling_avg_seconds is not None:
            line += f" avg_page={_format_duration(event.rolling_avg_seconds)}"
        if event.estimated_remaining_seconds is not None:
            line += f" eta={_format_duration(event.estimated_remaining_seconds)}"
        print(line, file=sys.stderr)
        return
    if event.kind == "warning":
        print(f"[{event.endpoint}] warning: {event.message}", file=sys.stderr)
        return
    if event.kind == "endpoint_finish":
        print(
            f"[{event.endpoint}] finish: pages={event.pages_fetched} items={event.items_fetched} "
            f"retries={event.retries_used} warnings={event.warnings_count} "
            f"elapsed={_format_seconds(event.elapsed_seconds)}",
            file=sys.stderr,
        )


def _print_human_fetch_summary(
    *,
    mode: str,
    run_id: str,
    raw_dir: Path,
    selected_count: int,
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    duration_seconds: float,
    ingest_result: IngestResult | None,
    changelog_run: ChangelogRun | None,
    changelog_skipped: str | None,
    pipeline_error: str | None,
) -> None:
    title = (
        "Fetch Complete"
        if not failures and not pipeline_error
        else "Fetch Finished With Failures"
    )
    print(title)
    print()
    print(f"Mode: {mode}")
    print(f"Run:  {run_id}")
    print(f"Raw:  {raw_dir}")
    print()
    print("Summary")
    print(f"Endpoints: {len(results)} ok, {len(failures)} failed, {selected_count} total")
    print(f"Records:   {sum(result.items_fetched for result in results)} fetched")
    print(f"Pages:     {sum(result.pages_fetched for result in results)} fetched")
    print(f"Time:      {_format_duration(duration_seconds)}")
    print(f"Retries:   {sum(result.retries_used for result in results)}")
    if results:
        endpoint_width = max(len("Endpoint"), *(len(result.endpoint) for result in results))
        header = f"{'Endpoint':<{endpoint_width}}  {'Records':>7}  {'Expected':>8}  {'Pages':>5}"
        print()
        print(header)
        print("-" * len(header))
        for result in results:
            expected = (
                str(result.expected_count) if result.expected_count is not None else "unknown"
            )
            print(
                f"{result.endpoint:<{endpoint_width}}  "
                f"{result.items_fetched:>7}  {expected:>8}  {result.pages_fetched:>5}"
            )
    if ingest_result is not None:
        print()
        print("Ingest")
        print(
            f"Files:     {ingest_result.applied_files} applied, "
            f"{ingest_result.skipped_files} skipped"
        )
        print(f"Records:   {ingest_result.records_read} read")
        print(f"Upserts:   {ingest_result.records_upserted}")
        print(f"Deletes:   {ingest_result.records_deleted}")
        if ingest_result.invalid_records:
            print(f"Invalid:   {ingest_result.invalid_records}")
    if changelog_run is not None:
        print()
        print("Changelog")
        print(f"Events:    {changelog_run.event_count}")
        print(f"Run:       {changelog_run.run_id}")
    elif changelog_skipped:
        print()
        print(f"Changelog: {changelog_skipped}.")
    if pipeline_error:
        print()
        print("Pipeline")
        print(f"- {pipeline_error}")
    if failures:
        print()
        print("Failures")
        for endpoint, message in failures:
            print(f"- {endpoint}: {message}")


def _print_json_fetch_records(
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    *,
    manifest_path: Path,
    ingest_result: IngestResult | None,
    changelog_run: ChangelogRun | None,
    changelog_skipped: str | None,
    pipeline_error: str | None,
) -> None:
    for result in results:
        print(
            json.dumps(
                {
                    "endpoint": result.endpoint,
                    "status": "ok",
                    "items_fetched": result.items_fetched,
                    "pages_fetched": result.pages_fetched,
                    "expected_count": result.expected_count,
                    "retries_used": result.retries_used,
                    "output_file": str(result.output_file),
                }
            )
        )
    for endpoint, message in failures:
        print(json.dumps({"endpoint": endpoint, "status": "failed", "error": message}))
    print(
        json.dumps(
            {
                "record_type": "pipeline_summary",
                "manifest": str(manifest_path),
                "ingest": _ingest_record(ingest_result),
                "changelog": _changelog_record(changelog_run, changelog_skipped),
                "pipeline_error": pipeline_error,
            }
        )
    )


def _write_run_manifest(
    *,
    output_dir: Path,
    run_id: str,
    mode: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    selected_specs: list[EndpointSpec],
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    endpoint_records: list[dict[str, Any]],
    modified_since: str | None,
) -> Path:
    status = (
        "OK"
        if not failures
        else ("FAILED" if len(failures) == len(selected_specs) else "PARTIAL")
    )
    manifest = {
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "started_at": _utc_iso(run_started_at),
        "finished_at": _utc_iso(run_finished_at),
        "duration_seconds": round((run_finished_at - run_started_at).total_seconds(), 3),
        "output_dir": str(output_dir),
        "selected_endpoints": [spec.name for spec in selected_specs],
        "endpoints_total": len(selected_specs),
        "endpoints_succeeded": len(results),
        "endpoints_failed": len(failures),
        "total_items": sum(result.items_fetched for result in results),
        "modified_since": modified_since,
        "failures": [{"endpoint": endpoint, "error": message} for endpoint, message in failures],
        "endpoints": {record["endpoint"]: record for record in endpoint_records},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    temp_path = output_dir / ".manifest.json.tmp"
    temp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(manifest_path)
    return manifest_path


def _endpoint_manifest_record(
    result: FetchRunResult,
    *,
    mode: str,
    status: str,
    attempt_start: str,
    attempt_end: str,
    delta_floor: str | None,
    modified_since: str | None,
) -> dict[str, Any]:
    return {
        "endpoint": result.endpoint,
        "file": result.output_file.name,
        "mode": mode,
        "status": status,
        "is_delta": mode == "delta",
        "delta_floor": delta_floor,
        "modified_since": modified_since,
        "attempt_start": attempt_start,
        "attempt_end": attempt_end,
        "items_fetched": result.items_fetched,
        "pages_fetched": result.pages_fetched,
        "expected_count": result.expected_count,
        "retries_used": result.retries_used,
        "warnings": result.warnings,
        "error": None,
    }


def _build_log_callback(
    log_file: TextIO,
    *,
    log_level: LogLevel,
) -> LogCallback:
    selected_rank = LOG_LEVEL_RANKS[log_level]

    def _log(event: LogEvent) -> None:
        event_level = str(event.get("level", "summary")).lower()
        event_rank = LOG_LEVEL_RANKS.get(event_level, LOG_LEVEL_RANKS["debug"])
        if event_rank > selected_rank:
            return
        record: LogEvent = {"timestamp": _utc_iso(), **event}
        url = record.get("url")
        if isinstance(url, str):
            record["url"] = _redact_url_query(url)
        line = _render_log_line(record)
        log_file.write(line + "\n")
        log_file.flush()

    return _log


def _render_log_line(record: LogEvent) -> str:
    pieces = [str(record.get("timestamp", "")), str(record.get("level", "summary")).upper()]
    pieces.append(str(record.get("event", "event")))
    for key in sorted(key for key in record if key not in {"timestamp", "level", "event"}):
        pieces.append(f"{key}={json.dumps(record[key], separators=(',', ':'), ensure_ascii=True)}")
    return " ".join(pieces)


def _redact_url_query(url: str) -> str:
    split_url = urlsplit(url)
    if not split_url.query:
        return url
    params = parse_qsl(split_url.query, keep_blank_values=True)
    changed = False
    redacted: list[tuple[str, str]] = []
    for key, value in params:
        if key.lower() in SENSITIVE_QUERY_KEYS:
            redacted.append((key, "***"))
            changed = True
        else:
            redacted.append((key, value))
    if not changed:
        return url
    return urlunsplit(
        (
            split_url.scheme,
            split_url.netloc,
            split_url.path,
            urlencode(redacted, doseq=True),
            split_url.fragment,
        )
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


def _print_delta_dry_run(
    spec: EndpointSpec,
    *,
    delta_floor: str | None,
    overlap_days: int,
    overlap_minutes: int,
) -> None:
    data_modified = spec.query_params.get("_modified_at=ge")
    count_modified = (
        spec.count_spec.query_params.get("_modified_at=ge") if spec.count_spec else None
    )
    print(
        json.dumps(
            {
                "endpoint": spec.name,
                "status": "delta_dry_run",
                "overlap_days": overlap_days,
                "overlap_minutes": overlap_minutes,
                "delta_floor": delta_floor,
                "data_modified_at": data_modified,
                "count_modified_at": count_modified,
            }
        )
    )


def _print_rows(rows: list[dict[str, Any]], as_json: bool, *, empty_message: str) -> int:
    if as_json:
        for row in rows:
            print(json.dumps(row, default=str))
        return 0
    if not rows:
        print(empty_message)
        return 0
    for row in rows:
        print(" ".join(f"{key}={json.dumps(value, default=str)}" for key, value in row.items()))
    return 0


def _print_or_json(as_json: bool, payload: dict[str, Any], message: str) -> None:
    print(json.dumps(payload, default=str) if as_json else message)


def _ingest_record(result: IngestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "applied_files": result.applied_files,
        "skipped_files": result.skipped_files,
        "records_read": result.records_read,
        "records_upserted": result.records_upserted,
        "records_deleted": result.records_deleted,
        "invalid_records": result.invalid_records,
    }


def _changelog_record(run: ChangelogRun | None, skipped: str | None) -> dict[str, Any]:
    if run is None:
        return {"status": "skipped", "reason": skipped}
    return {
        "status": "updated",
        "run_id": run.run_id,
        "endpoint_count": run.endpoint_count,
        "record_count": run.record_count,
        "event_count": run.event_count,
        "full_refresh": run.full_refresh,
    }


def _db_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else runtime_path(DEFAULT_DB_PATH)


def _classify_status(result: FetchRunResult, error: str | None) -> str:
    if error is not None:
        return "FAILED"
    if result.warnings:
        return "PARTIAL"
    return "OK"


def _append_human_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_render_log_line({"timestamp": _utc_iso(), **record}) + "\n")


def _append_human_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{_utc_iso()} SUMMARY {message}\n")


def _parse_days_back(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--days must be an integer.") from exc
    if parsed < MIN_DAYS_BACK or parsed > MAX_DAYS_BACK:
        raise argparse.ArgumentTypeError(
            f"--days must be between {MIN_DAYS_BACK} and {MAX_DAYS_BACK}."
        )
    return parsed


def _parse_months_back(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--months must be an integer.") from exc
    if parsed < MIN_MONTHS_BACK or parsed > MAX_MONTHS_BACK:
        raise argparse.ArgumentTypeError(
            f"--months must be between {MIN_MONTHS_BACK} and {MAX_MONTHS_BACK}."
        )
    return parsed


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


def _parse_utc_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _format_seconds(value: float | None) -> str:
    seconds = value if value is not None else 0.0
    return f"{seconds:.2f}s"


def _format_duration(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1:
        return f"{value * 1000:.0f}ms"
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(int(round(value)), 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


if __name__ == "__main__":
    raise SystemExit(main())
