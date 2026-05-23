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
from ..config import ConfigError, runtime_path
from ..defaults import DEFAULT_BUNDLE_LOCK_PATH
from ..defaults import db_path as resolve_db_path
from ..rendering.bundle import (
    bundle_comparison_record,
    bundle_record,
    print_human_bundle_changelog,
    print_human_bundle_list,
    print_human_bundle_show,
    print_human_bundle_summary,
)
from ..rendering.common import print_rows
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
        print(json.dumps(bundle_record(result), default=str))
    elif not args.quiet:
        print_human_bundle_summary(result)
    return 1 if result.missing_count else 0


def _run_bundle_history(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
    if args.action == "list":
        rows = list_bundle_runs(db_path, bundle_name=args.job, limit=args.limit)
        if args.json:
            return print_rows(rows, True, empty_message="No bundle runs found.")
        if not rows:
            print("No bundle runs found.")
            return 0
        print_human_bundle_list(rows)
        return 0
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
            print_human_bundle_show(run, items)
        return 0
    comparison = compare_bundle_runs(
        db_path,
        from_run_id=args.bundle_run_id,
        to_run_id=args.to,
    )
    if args.json:
        print(json.dumps(bundle_comparison_record(comparison), default=str))
    else:
        print_human_bundle_changelog(comparison)
    return 0
