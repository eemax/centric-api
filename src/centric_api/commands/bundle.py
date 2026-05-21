from __future__ import annotations

import argparse
import json
import sys

from ..bundle import run_bundle_job
from ..bundle_config import load_bundle_config
from ..bundle_state import (
    compare_bundle_runs,
    get_bundle_run,
    list_bundle_items,
    list_bundle_runs,
)
from ..cli_output import (
    _bundle_comparison_record,
    _bundle_record,
    _print_human_bundle_changelog,
    _print_human_bundle_show,
    _print_human_bundle_summary,
    _print_rows,
)
from ..config import ConfigError, runtime_path
from ..defaults import DEFAULT_BUNDLE_LOCK_PATH
from ..defaults import db_path as resolve_db_path
from .common import release_bundle_lock, try_acquire_bundle_lock


def run_bundle(args: argparse.Namespace) -> int:
    if args.action != "run":
        return _run_bundle_history(args)
    if args.dry_run:
        return _run_bundle_unlocked(args)
    lock_file = runtime_path(DEFAULT_BUNDLE_LOCK_PATH)
    lock_error = try_acquire_bundle_lock(lock_file)
    if lock_error is not None:
        print(f"Error: {lock_error}", file=sys.stderr)
        return 1
    try:
        return _run_bundle_unlocked(args)
    finally:
        release_bundle_lock(lock_file)


def _run_bundle_unlocked(args: argparse.Namespace) -> int:
    result = run_bundle_job(
        db_path=resolve_db_path(args.db),
        config=load_bundle_config(args.bundle_config),
        job_name=args.job,
        dry_run=args.dry_run,
        zip_bundle=not args.no_zip,
    )
    if args.json:
        print(json.dumps(_bundle_record(result), default=str))
    elif not args.quiet:
        _print_human_bundle_summary(result)
    return 1 if result.missing_count else 0


def _run_bundle_history(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
    if args.action == "list":
        rows = list_bundle_runs(db_path, bundle_name=args.job, limit=args.limit)
        return _print_rows(rows, args.json, empty_message="No bundle runs found.")
    if args.bundle_run_id is None:
        raise ConfigError(f"bundle {args.action} requires a bundle run id.")
    if args.action == "show":
        run = get_bundle_run(db_path, args.bundle_run_id)
        if run is None:
            raise ConfigError(
                f"Unknown bundle run id: {args.bundle_run_id}. Run centric-api bundle list."
            )
        items = list_bundle_items(db_path, args.bundle_run_id)
        payload = {"run": run, "items": items}
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_human_bundle_show(run, items)
        return 0
    comparison = compare_bundle_runs(
        db_path,
        from_run_id=args.bundle_run_id,
        to_run_id=args.to,
    )
    if args.json:
        print(json.dumps(_bundle_comparison_record(comparison), default=str))
    else:
        _print_human_bundle_changelog(comparison)
    return 0
