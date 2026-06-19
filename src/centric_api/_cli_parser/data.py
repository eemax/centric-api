from __future__ import annotations

import argparse

from ..defaults import DEFAULT_CONFIG_PATH
from ..rendering.logs import LOG_LEVEL_RANKS
from .common import _parse_days_back, _parse_months_back, _parse_positive_int


def add_fetch_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    fetch_parser = subparsers.add_parser("fetch", help="Fetch Centric API records")
    fetch_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path.",
    )
    fetch_parser.add_argument(
        "--endpoint",
        metavar="NAME",
        action="append",
        default=[],
        help="Endpoint to fetch; repeat for multiple endpoints.",
    )
    fetch_parser.add_argument("--full", action="store_true", help="Refetch all records.")
    fetch_parser.add_argument(
        "--days",
        metavar="N",
        type=_parse_days_back,
        default=None,
        help="Fetch records modified in the last N days.",
    )
    fetch_parser.add_argument(
        "--months",
        metavar="N",
        type=_parse_months_back,
        default=None,
        help="Fetch records modified in the last N calendar months.",
    )
    fetch_parser.add_argument("--resume", action="store_true", help="Resume from checkpoints.")
    fetch_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    fetch_parser.add_argument(
        "--schema",
        metavar="PATH",
        default=None,
        help="Endpoint schema config path.",
    )
    fetch_parser.add_argument(
        "--delta-state-file",
        metavar="PATH",
        default=None,
        help="Delta state file path.",
    )
    fetch_parser.add_argument(
        "--delta-dry-run",
        action="store_true",
        help="Print derived delta filters without fetching.",
    )
    fetch_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    fetch_parser.add_argument("--quiet", action="store_true", help="Suppress human progress.")
    fetch_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")
    fetch_parser.add_argument(
        "--log-level",
        choices=list(LOG_LEVEL_RANKS),
        default="summary",
        help="Fetch log verbosity.",
    )

def add_ingest_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ingest_parser = subparsers.add_parser("ingest", help="Inspect or ingest raw evidence")
    ingest_actions = ingest_parser.add_subparsers(dest="action", required=True)

    ingest_check_parser = ingest_actions.add_parser("check", help="Validate a raw run")
    ingest_check_parser.add_argument("raw_run", metavar="RAW_RUN", help="Raw run id or path.")
    ingest_check_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    ingest_check_parser.add_argument(
        "--schema",
        metavar="PATH",
        default=None,
        help="Endpoint schema config path.",
    )
    ingest_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    ingest_raw_run_parser = ingest_actions.add_parser("raw-run", help="Ingest one raw run")
    ingest_raw_run_parser.add_argument("raw_run", metavar="RAW_RUN", help="Raw run id or path.")
    ingest_raw_run_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    ingest_raw_run_parser.add_argument(
        "--schema",
        metavar="PATH",
        default=None,
        help="Endpoint schema config path.",
    )
    ingest_raw_run_parser.add_argument(
        "--changelog",
        action="store_true",
        help="Run the normal scoped changelog after ingest.",
    )
    ingest_raw_run_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

def add_raw_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    raw_parser = subparsers.add_parser("raw", help="Inspect and compact raw evidence")
    raw_actions = raw_parser.add_subparsers(dest="action", required=True)

    raw_check_parser = raw_actions.add_parser("check", help="Verify raw evidence indexes")
    raw_check_parser.add_argument(
        "raw_run",
        metavar="RAW_RUN",
        nargs="?",
        default=None,
        help="Raw run id or path. Omit to check all runs under raw/runs.",
    )
    raw_check_parser.add_argument(
        "--raw-dir", metavar="PATH", default=None, help="Raw root directory."
    )
    raw_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    raw_index_parser = raw_actions.add_parser("index", help="Repair missing raw evidence indexes")
    raw_index_parser.add_argument(
        "raw_run",
        metavar="RAW_RUN",
        nargs="?",
        default=None,
        help="Raw run id or path.",
    )
    raw_index_parser.add_argument(
        "--all",
        action="store_true",
        help="Repair missing indexes for all trusted runs under raw/runs.",
    )
    raw_index_parser.add_argument(
        "--raw-dir", metavar="PATH", default=None, help="Raw root directory."
    )
    raw_index_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    raw_inspect_parser = raw_actions.add_parser(
        "inspect",
        help="Show raw observations for a record",
    )
    raw_inspect_parser.add_argument("endpoint", metavar="ENDPOINT", help="Endpoint name.")
    raw_inspect_parser.add_argument("record_id", metavar="RECORD_ID", help="Record id.")
    raw_inspect_parser.add_argument(
        "--hash", metavar="SHA", default=None, help="Filter to a payload hash prefix."
    )
    raw_inspect_parser.add_argument(
        "--latest", action="store_true", help="Show only the latest observation."
    )
    raw_inspect_parser.add_argument(
        "--show-payload", action="store_true", help="Include raw payload JSON."
    )
    raw_inspect_parser.add_argument(
        "--raw-dir", metavar="PATH", default=None, help="Raw root directory."
    )
    raw_inspect_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    raw_diff_parser = raw_actions.add_parser("diff", help="Diff raw payload observations")
    raw_diff_parser.add_argument("endpoint", metavar="ENDPOINT", help="Endpoint name.")
    raw_diff_parser.add_argument("record_id", metavar="RECORD_ID", help="Record id.")
    raw_diff_parser.add_argument(
        "--from", dest="from_hash", metavar="SHA", default=None, help="Source payload hash prefix."
    )
    raw_diff_parser.add_argument(
        "--to", dest="to_hash", metavar="SHA", default=None, help="Target payload hash prefix."
    )
    raw_diff_parser.add_argument(
        "--raw-dir", metavar="PATH", default=None, help="Raw root directory."
    )
    raw_diff_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    raw_compact_parser = raw_actions.add_parser(
        "compact",
        help="Create a compacted full raw run from indexed evidence",
    )
    raw_compact_parser.add_argument(
        "--raw-dir", metavar="PATH", default=None, help="Raw root directory."
    )
    raw_compact_parser.add_argument(
        "--output", metavar="PATH", default=None, help="Compacted run output directory."
    )
    raw_compact_parser.add_argument(
        "--schema", metavar="PATH", default=None, help="Endpoint schema config path."
    )
    raw_compact_parser.add_argument(
        "--archive-old",
        action="store_true",
        help="Move source runs to raw/archive after compacting.",
    )
    raw_compact_parser.add_argument("--dry-run", action="store_true", help="Plan without writing.")
    raw_compact_parser.add_argument(
        "--exact",
        action="store_true",
        help="Hydrate payloads during dry-run to count written/deleted winners exactly.",
    )
    raw_compact_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

def add_changelog_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    changelog_parser = subparsers.add_parser("changelog", help="Inspect or update changelog")
    changelog_parser.add_argument(
        "action",
        nargs="?",
        choices=[
            "summary",
            "actors",
            "leaderboard",
            "runs",
            "changes",
            "update",
            "prune",
        ],
        default="summary",
        help="Changelog action to run.",
    )
    changelog_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    changelog_parser.add_argument(
        "--endpoint",
        metavar="NAME",
        action="append",
        default=[],
        help="Endpoint filter. Repeatable for update; read views accept one endpoint.",
    )
    changelog_parser.add_argument(
        "--since",
        metavar="VALUE",
        default=None,
        help="Relative window like 7d/24h/10m or an ISO timestamp.",
    )
    changelog_parser.add_argument(
        "--older-than",
        metavar="VALUE",
        default=None,
        help="Prune changelog history older than a relative window or ISO timestamp.",
    )
    changelog_parser.add_argument(
        "--limit",
        metavar="N",
        type=_parse_positive_int,
        default=50,
        help="Row limit.",
    )
    changelog_parser.add_argument(
        "--include-payloads",
        action="store_true",
        help="Store previous/current payload snapshots during changelog update.",
    )
    changelog_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

def add_download_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    download_parser = subparsers.add_parser("download", help="Download latest document revisions")
    download_parser.add_argument(
        "--download-config",
        metavar="PATH",
        default=None,
        help="Download config path.",
    )
    download_parser.add_argument("--job", metavar="NAME", default=None, help="Download job name.")
    download_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    download_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path for credentials.",
    )
    download_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    download_parser.add_argument("--dry-run", action="store_true", help="Plan without downloading.")
    download_mode = download_parser.add_mutually_exclusive_group()
    download_mode.add_argument("--sync", action="store_true", help="Verify selected current files.")
    download_mode.add_argument("--rebuild", action="store_true", help="Redownload selected files.")
    download_parser.add_argument("--quiet", action="store_true", help="Suppress human output.")
    download_parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    download_parser.add_argument(
        "--log-level",
        choices=list(LOG_LEVEL_RANKS),
        default="summary",
        help="Download log verbosity.",
    )

def add_bundle_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    bundle_parser = subparsers.add_parser("bundle", help="Package downloaded files into a bundle")
    bundle_actions = bundle_parser.add_subparsers(dest="action", required=True)

    bundle_run_parser = bundle_actions.add_parser("run", help="Package downloaded files")
    bundle_run_parser.add_argument(
        "--bundle-config", metavar="PATH", default=None, help="Bundle config path."
    )
    bundle_run_parser.add_argument("--job", metavar="NAME", default=None, help="Bundle job name.")
    bundle_run_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_run_parser.add_argument(
        "--dry-run", action="store_true", help="Plan without writing bundle state."
    )
    bundle_run_parser.add_argument(
        "--no-zip", action="store_true", help="Skip ZIP archive creation."
    )
    bundle_run_parser.add_argument("--quiet", action="store_true", help="Suppress human output.")
    bundle_run_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    bundle_list_parser = bundle_actions.add_parser("list", help="List bundle runs")
    bundle_list_parser.add_argument(
        "--job", metavar="NAME", default=None, help="Bundle job filter."
    )
    bundle_list_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_list_parser.add_argument("--limit", metavar="N", type=int, default=50, help="Run limit.")
    bundle_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    bundle_show_parser = bundle_actions.add_parser("show", help="Show one bundle run")
    bundle_show_parser.add_argument("bundle_run_id", metavar="RUN_ID", help="Bundle run id.")
    bundle_show_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    bundle_changelog_parser = bundle_actions.add_parser(
        "changelog",
        help="Compare a received bundle run with a later run",
    )
    bundle_changelog_parser.add_argument(
        "bundle_run_id", metavar="RUN_ID", help="Source bundle run id."
    )
    bundle_changelog_parser.add_argument(
        "--to", metavar="RUN_ID", default="latest", help="Comparison target run."
    )
    bundle_changelog_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_changelog_parser.add_argument(
        "--json", action="store_true", help="Emit one JSON object."
    )

