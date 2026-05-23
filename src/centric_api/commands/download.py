from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from ..auth import init_auth_context
from ..config import load_fetcher_settings, runtime_path
from ..defaults import DEFAULT_DOWNLOAD_LOCK_PATH, DEFAULT_DOWNLOAD_LOG_PATH
from ..defaults import db_path as resolve_db_path
from ..download import run_download_job
from ..download_config import load_download_config
from ..rendering.download import (
    download_record,
    print_human_download_summary,
    write_download_progress_line,
    write_json_download_progress,
)
from ..rendering.logs import LogCallback, build_log_callback
from .common import release_download_lock, try_acquire_download_lock, utc_iso


def run_download(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_download_unlocked(args)
    lock_file = runtime_path(DEFAULT_DOWNLOAD_LOCK_PATH)
    lock_error = try_acquire_download_lock(lock_file)
    if lock_error is not None:
        print(f"Error: {lock_error}", file=sys.stderr)
        return 1
    try:
        return _run_download_unlocked(args)
    finally:
        release_download_lock(lock_file)


def _run_download_unlocked(args: argparse.Namespace) -> int:
    config = load_download_config(args.download_config)
    db_path = resolve_db_path(args.db)
    mode = "rebuild" if args.rebuild else ("sync" if args.sync else "delta")
    progress_callback = None
    if args.json:
        progress_callback = write_json_download_progress
    elif not args.quiet:
        progress_callback = write_download_progress_line
    download_log_file: TextIO | None = None
    log_callback: LogCallback | None = None
    if not args.dry_run and args.log_level != "off":
        log_path = runtime_path(DEFAULT_DOWNLOAD_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        download_log_file = log_path.open("a", encoding="utf-8")
        log_callback = build_log_callback(
            download_log_file,
            log_level=args.log_level,
            utc_iso=utc_iso,
        )
    try:
        if args.dry_run:
            result = run_download_job(
                db_path=db_path,
                auth_ctx=None,
                config=config,
                job_name=args.job,
                mode=mode,
                dry_run=True,
                log_callback=log_callback,
                progress_callback=progress_callback,
            )
        else:
            _fetcher_cfg, auth_settings, _endpoint_specs = load_fetcher_settings(args.fetch_config)
            with init_auth_context(
                auth_settings,
                env_file=Path(args.env_file).expanduser() if args.env_file else None,
            ) as auth_ctx:
                result = run_download_job(
                    db_path=db_path,
                    auth_ctx=auth_ctx,
                    config=config,
                    job_name=args.job,
                    mode=mode,
                    dry_run=False,
                    log_callback=log_callback,
                    progress_callback=progress_callback,
                )
    finally:
        if download_log_file is not None:
            download_log_file.close()
    if args.json:
        print(json.dumps(download_record(result), default=str))
    elif not args.quiet:
        print_human_download_summary(result)
    return 1 if result.failed_count else 0
