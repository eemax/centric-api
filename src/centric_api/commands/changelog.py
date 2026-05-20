from __future__ import annotations

import argparse

from ..changelog import (
    list_actor_summary,
    list_change_summary,
    list_changelog_runs,
    list_changes,
    list_field_summary,
    parse_since,
    record_changelog,
)
from ..cli_output import _print_or_json, _print_rows
from ..defaults import db_path as resolve_db_path


def run_changelog(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
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
                "scoped_record_count": run.scoped_record_count,
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
    if args.action == "actors":
        rows = list_actor_summary(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=args.limit,
        )
        return _print_rows(rows, args.json, empty_message="No changelog actor changes found.")
    rows = list_change_summary(db_path, since=since, limit=args.limit)
    return _print_rows(rows, args.json, empty_message="No changelog events found.")
