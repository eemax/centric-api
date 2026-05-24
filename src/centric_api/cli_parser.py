from __future__ import annotations

import argparse
import sys

from .defaults import (
    DEFAULT_CONFIG_PATH,
    MAX_DAYS_BACK,
    MAX_MONTHS_BACK,
    MIN_DAYS_BACK,
    MIN_MONTHS_BACK,
)
from .rendering.logs import LOG_LEVEL_RANKS


def normalize_argv(argv: list[str] | None) -> list[str] | None:
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] != ["bundle"]:
        return argv
    if len(args) == 1:
        return ["bundle", "run"]
    next_arg = args[1]
    bundle_actions = {"run", "list", "show", "changelog"}
    if next_arg not in bundle_actions and next_arg not in {"-h", "--help"}:
        return [args[0], "run", *args[1:]]
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="centric-api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch Centric API records")
    fetch_parser.add_argument("--fetch-config", default=str(DEFAULT_CONFIG_PATH))
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
    fetch_parser.add_argument("--quiet", action="store_true")
    fetch_parser.add_argument("--json", action="store_true")
    fetch_parser.add_argument("--log-level", choices=list(LOG_LEVEL_RANKS), default="summary")

    changelog_parser = subparsers.add_parser("changelog", help="Inspect or update changelog")
    changelog_parser.add_argument(
        "action",
        nargs="?",
        choices=["summary", "fields", "actors", "leaderboard", "runs", "changes", "update"],
        default="summary",
    )
    changelog_parser.add_argument("--db", default=None)
    changelog_parser.add_argument("--endpoint", action="append", default=[])
    changelog_parser.add_argument("--since", default=None)
    changelog_parser.add_argument("--limit", type=int, default=50)
    changelog_parser.add_argument("--json", action="store_true")

    download_parser = subparsers.add_parser("download", help="Download latest document revisions")
    download_parser.add_argument("--download-config", default=None)
    download_parser.add_argument("--job", default=None)
    download_parser.add_argument("--db", default=None)
    download_parser.add_argument("--fetch-config", default=str(DEFAULT_CONFIG_PATH))
    download_parser.add_argument("--env-file", default=None)
    download_parser.add_argument("--dry-run", action="store_true")
    download_mode = download_parser.add_mutually_exclusive_group()
    download_mode.add_argument("--sync", action="store_true")
    download_mode.add_argument("--rebuild", action="store_true")
    download_parser.add_argument("--quiet", action="store_true")
    download_parser.add_argument("--json", action="store_true")
    download_parser.add_argument("--log-level", choices=list(LOG_LEVEL_RANKS), default="summary")

    bundle_parser = subparsers.add_parser("bundle", help="Package downloaded files into a bundle")
    bundle_actions = bundle_parser.add_subparsers(dest="action", required=True)

    bundle_run_parser = bundle_actions.add_parser("run", help="Package downloaded files")
    bundle_run_parser.add_argument("--bundle-config", default=None)
    bundle_run_parser.add_argument("--job", default=None)
    bundle_run_parser.add_argument("--db", default=None)
    bundle_run_parser.add_argument("--dry-run", action="store_true")
    bundle_run_parser.add_argument("--no-zip", action="store_true")
    bundle_run_parser.add_argument("--quiet", action="store_true")
    bundle_run_parser.add_argument("--json", action="store_true")

    bundle_list_parser = bundle_actions.add_parser("list", help="List bundle runs")
    bundle_list_parser.add_argument("--job", default=None)
    bundle_list_parser.add_argument("--db", default=None)
    bundle_list_parser.add_argument("--limit", type=int, default=50)
    bundle_list_parser.add_argument("--json", action="store_true")

    bundle_show_parser = bundle_actions.add_parser("show", help="Show one bundle run")
    bundle_show_parser.add_argument("bundle_run_id")
    bundle_show_parser.add_argument("--db", default=None)
    bundle_show_parser.add_argument("--json", action="store_true")

    bundle_changelog_parser = bundle_actions.add_parser(
        "changelog",
        help="Compare a received bundle run with a later run",
    )
    bundle_changelog_parser.add_argument("bundle_run_id")
    bundle_changelog_parser.add_argument("--to", default="latest")
    bundle_changelog_parser.add_argument("--db", default=None)
    bundle_changelog_parser.add_argument("--json", action="store_true")

    view_parser = subparsers.add_parser("view", help="Export configured tabular cache views")
    view_actions = view_parser.add_subparsers(dest="action", required=True)

    view_list_parser = view_actions.add_parser("list", help="List configured views")
    view_list_parser.add_argument("--view-config", default=None)
    view_list_parser.add_argument("--json", action="store_true")

    view_show_parser = view_actions.add_parser("show", help="Show one configured view")
    view_show_parser.add_argument("name")
    view_show_parser.add_argument("--view-config", default=None)
    view_show_parser.add_argument("--json", action="store_true")

    view_check_parser = view_actions.add_parser("check", help="Check a configured view")
    view_check_parser.add_argument("name")
    view_check_parser.add_argument("--view-config", default=None)
    view_check_parser.add_argument("--db", default=None)
    view_check_parser.add_argument("--json", action="store_true")

    view_export_parser = view_actions.add_parser("export", help="Export a configured view")
    view_export_parser.add_argument("name")
    view_export_parser.add_argument("--view-config", default=None)
    view_export_parser.add_argument("--db", default=None)
    view_export_parser.add_argument("--format", choices=["xlsx", "csv"], default=None)
    view_export_parser.add_argument("--output", default=None)
    view_export_parser.add_argument("--json", action="store_true")

    units_parser = subparsers.add_parser("units", help="Inspect and convert configured units")
    units_parser.add_argument("--units-config", default=None)
    units_actions = units_parser.add_subparsers(dest="action", required=True)

    units_list_parser = units_actions.add_parser("list", help="List unit dimensions")
    units_list_parser.add_argument("--json", action="store_true")

    units_show_parser = units_actions.add_parser("show", help="Show one unit dimension")
    units_show_parser.add_argument("dimension")
    units_show_parser.add_argument("--json", action="store_true")

    units_normalize_parser = units_actions.add_parser("normalize", help="Normalize a unit label")
    units_normalize_parser.add_argument("unit")
    units_normalize_parser.add_argument("--json", action="store_true")

    units_convert_parser = units_actions.add_parser("convert", help="Convert a value between units")
    units_convert_parser.add_argument("value")
    units_convert_parser.add_argument("from_unit")
    units_convert_parser.add_argument("to_unit")
    units_convert_parser.add_argument("--json", action="store_true")

    units_check_parser = units_actions.add_parser("check", help="Validate unit registry")
    units_check_parser.add_argument("--json", action="store_true")

    cron_parser = subparsers.add_parser("cron", help="Run scheduled delta fetches in foreground")
    cron_parser.add_argument("schedule", nargs="?", default="0 * * * *")
    cron_parser.add_argument("--run-now", action="store_true")
    cron_parser.add_argument("--endpoint", action="append", default=[])
    cron_parser.add_argument("--fetch-config", default=str(DEFAULT_CONFIG_PATH))
    cron_parser.add_argument("--db", default=None)
    cron_parser.add_argument("--schema", default=None)
    cron_parser.add_argument("--delta-state-file", default=None)
    cron_parser.add_argument("--env-file", default=None)

    status_parser = subparsers.add_parser("status", help="Show local Centric API status")
    status_parser.add_argument("--db", default=None)
    status_parser.add_argument("--json", action="store_true")

    doctor_parser = subparsers.add_parser("doctor", help="Check local Centric API setup")
    doctor_parser.add_argument("--fetch-config", default=str(DEFAULT_CONFIG_PATH))
    doctor_parser.add_argument("--download-config", default=None)
    doctor_parser.add_argument("--bundle-config", default=None)
    doctor_parser.add_argument("--schema", default=None)
    doctor_parser.add_argument("--db", default=None)
    doctor_parser.add_argument("--env-file", default=None)
    doctor_parser.add_argument("--json", action="store_true")

    rebuild_parser = subparsers.add_parser("rebuild-db", help="Rebuild SQLite from raw evidence")
    rebuild_parser.add_argument("--db", default=None)
    rebuild_parser.add_argument("--raw-dir", default=None)
    rebuild_parser.add_argument("--schema", default=None)
    rebuild_parser.add_argument("--yes", action="store_true")
    rebuild_parser.add_argument("--json", action="store_true")
    return parser


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
