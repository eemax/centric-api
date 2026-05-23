from __future__ import annotations

import argparse

from ..changelog import (
    list_actor_leaderboard,
    list_actor_summary,
    list_actor_totals,
    list_change_summary,
    list_changelog_runs,
    list_changes,
    list_field_summary,
    parse_since,
    record_changelog,
)
from ..defaults import db_path as resolve_db_path
from ..rendering.changelog import (
    print_human_changelog_actor_summary,
    print_human_changelog_changes,
    print_human_changelog_field_summary,
    print_human_changelog_leaderboard,
    print_human_changelog_runs,
    print_human_changelog_summary,
)
from ..rendering.common import print_or_json, print_rows


def run_changelog(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
    since = parse_since(args.since)
    if args.action == "update":
        run = record_changelog(
            db_path,
            endpoints=set(args.endpoint) if args.endpoint else None,
            full=True,
        )
        print_or_json(
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
        if args.json:
            return print_rows(rows, True, empty_message="No changelog runs found.")
        if not rows:
            print("No changelog runs found.")
            return 0
        print_human_changelog_runs(rows, since=args.since)
        return 0
    if args.action == "changes":
        rows = list_changes(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=args.limit,
        )
        if args.json:
            return print_rows(rows, True, empty_message="No changelog changes found.")
        if not rows:
            print("No changelog changes found.")
            return 0
        print_human_changelog_changes(
            rows,
            since=args.since,
            endpoint=args.endpoint[0] if args.endpoint else None,
        )
        return 0
    if args.action == "fields":
        rows = list_field_summary(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=args.limit if args.json or args.endpoint else 10000,
        )
        if args.json:
            return print_rows(rows, True, empty_message="No changelog field changes found.")
        if not rows:
            print("No changelog field changes found.")
            return 0
        change_rows = list_change_summary(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=10000,
        )
        print_human_changelog_field_summary(
            rows,
            change_rows,
            since=args.since,
            endpoint=args.endpoint[0] if args.endpoint else None,
        )
        return 0
    if args.action == "actors":
        rows = list_actor_summary(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
            limit=args.limit,
        )
        if args.json:
            return print_rows(rows, True, empty_message="No changelog actor changes found.")
        if not rows:
            print("No changelog actor changes found.")
            return 0
        print_human_changelog_actor_summary(
            rows,
            since=args.since,
            endpoint=args.endpoint[0] if args.endpoint else None,
        )
        return 0
    if args.action == "leaderboard":
        rows = list_actor_leaderboard(
            db_path,
            endpoint=args.endpoint[0] if args.endpoint else None,
            since=since,
        )
        displayed_rows = rows[: max(args.limit, 0)]
        if args.json:
            return print_rows(
                displayed_rows,
                True,
                empty_message="No changelog leaderboard entries found.",
            )
        if not rows:
            print("No changelog leaderboard entries found.")
            return 0
        print_human_changelog_leaderboard(
            rows,
            since=args.since,
            endpoint=args.endpoint[0] if args.endpoint else None,
            limit=args.limit,
        )
        return 0
    endpoint = args.endpoint[0] if args.endpoint else None
    rows = list_change_summary(
        db_path,
        endpoint=endpoint,
        since=since,
        limit=args.limit if args.json else 10000,
    )
    if args.json:
        return print_rows(rows, True, empty_message="No changelog events found.")
    if not rows:
        print("No changelog events found.")
        return 0
    actors = list_actor_totals(
        db_path,
        endpoint=endpoint,
        since=since,
        limit=10,
    )
    print_human_changelog_summary(
        rows,
        actors,
        since=args.since,
        endpoint=endpoint,
        limit=args.limit,
    )
    return 0
