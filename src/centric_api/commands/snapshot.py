from __future__ import annotations

import argparse
import json

from ..config import ConfigError
from ..defaults import db_path as resolve_db_path
from ..rendering.common import print_rows
from ..rendering.snapshot import (
    print_human_snapshot_diff,
    print_human_snapshot_list,
    print_human_snapshot_show,
    print_human_snapshot_summary,
    snapshot_diff_record,
    snapshot_record,
    snapshot_summary_record,
)
from ..snapshot.registry import discover_snapshots, select_snapshot
from ..snapshot.runner import build_snapshot, check_snapshot, diff_snapshot, promote_snapshot


def run_snapshot_command(args: argparse.Namespace) -> int:
    snapshots = discover_snapshots(args.snapshots_dir)
    if args.action == "list":
        rows = [snapshot_record(snapshot) for snapshot in snapshots]
        if args.json:
            return print_rows(rows, True, empty_message="No snapshots found.")
        print_human_snapshot_list(snapshots)
        return 0

    snapshot = select_snapshot(snapshots, args.name)
    if args.action == "show":
        payload = snapshot_record(snapshot)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print_human_snapshot_show(snapshot)
        return 0

    if args.action == "check":
        summary = check_snapshot(
            resolve_db_path(args.db),
            snapshot,
            units_config=args.units_config,
        )
    elif args.action == "build":
        summary = build_snapshot(
            resolve_db_path(args.db),
            snapshot,
            output_root=args.output_dir,
            target=args.target,
            units_config=args.units_config,
            clean=args.clean,
        )
    elif args.action == "diff":
        diff = diff_snapshot(
            snapshot,
            output_root=args.output_dir,
            review_file=args.review_file,
            db_path=resolve_db_path(args.db),
            require_db=args.db is not None,
        )
        if args.json:
            print(json.dumps(snapshot_diff_record(diff), default=str))
        else:
            print_human_snapshot_diff(diff)
        return 0
    else:
        if args.review_file is None and not args.yes:
            raise ConfigError(
                "Full snapshot promotion requires --yes. "
                "Use --review-file PATH for selective promotion."
            )
        summary = promote_snapshot(
            snapshot,
            output_root=args.output_dir,
            clean=args.clean,
            review_file=args.review_file,
        )
    if args.json:
        print(json.dumps(snapshot_summary_record(summary), default=str))
    else:
        print_human_snapshot_summary(summary)
    return 0
