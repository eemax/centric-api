from __future__ import annotations

import argparse
import contextlib
import io
import time
from datetime import datetime
from pathlib import Path

from croniter import croniter

from ..cli_output import _format_duration
from ..config import ConfigError, runtime_path
from ..defaults import DEFAULT_CRON_LOG_PATH, DEFAULT_LOCK_PATH
from ..runtime_io import parse_jsonl, safe_int
from .common import (
    append_cron_log_event,
    append_cron_log_fetch_records,
    release_fetch_lock,
    try_acquire_fetch_lock,
    utc_iso,
)
from .fetch import run_fetch


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
    append_cron_log_event(log_file, record_type="cron_start", schedule=schedule)

    try:
        if args.run_now:
            run_cron_fetch_once(args, lock_file=lock_file, log_file=log_file)
        while True:
            next_run = croniter(schedule, datetime.now().astimezone()).get_next(datetime)
            wait_seconds = max(0.0, (next_run - datetime.now().astimezone()).total_seconds())
            print(f"Next fetch: {next_run.astimezone().isoformat(timespec='seconds')}")
            time.sleep(wait_seconds)
            run_cron_fetch_once(args, lock_file=lock_file, log_file=log_file)
    except KeyboardInterrupt:
        print("Cron stopped.")
        append_cron_log_event(log_file, record_type="cron_stop")
        return 0


def run_cron_fetch_once(args: argparse.Namespace, *, lock_file: Path, log_file: Path) -> None:
    lock_error = try_acquire_fetch_lock(lock_file)
    if lock_error is not None:
        print(f"Skipping fetch; {lock_error}")
        append_cron_log_event(
            log_file,
            record_type="cron_fetch_skipped",
            reason="lock_exists",
            lock_file=str(lock_file),
            message=lock_error,
        )
        return
    started = time.time()
    print(f"Fetch starting: {utc_iso()}")
    try:
        fetch_args = argparse.Namespace(
            command="fetch",
            fetch_config=args.fetch_config,
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
            quiet=True,
            json=True,
            log_level="off",
            skip_fetch_lock=True,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exit_code = run_fetch(fetch_args)
            except Exception as exc:
                exit_code = 1
                print(f"Error: {exc}", file=stderr)
        duration = time.time() - started
        fetch_records = parse_jsonl(stdout.getvalue())
        append_cron_log_fetch_records(
            log_file,
            records=fetch_records,
            stderr=stderr.getvalue(),
            exit_code=exit_code,
            duration_seconds=duration,
        )
        ok_count = sum(1 for record in fetch_records if record.get("status") == "ok")
        failed_count = sum(1 for record in fetch_records if record.get("status") == "failed")
        total_items = sum(safe_int(record.get("items_fetched")) for record in fetch_records)
        print(f"Fetch finished: exit={exit_code} duration={_format_duration(duration)}")
        print(f"Fetch records: {ok_count} ok, {failed_count} failed, {total_items} items fetched")
    finally:
        release_fetch_lock(lock_file)
