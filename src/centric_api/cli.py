from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__
from .client import AuthError, FetchError
from .config import ConfigError, load_settings
from .db import DatabaseError, LockError
from .fetch import FetchSummary, run_fetch
from .maintenance import collect_status, run_silver, run_swagger
from .silver import SilverResult


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except (ConfigError, AuthError, FetchError, LockError, DatabaseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Error: database: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. The next fetch resumes where this one stopped.", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="centric-api",
        description="Fetch Centric PLM data into a local SQLite database (bronze and silver).",
    )
    parser.add_argument("--version", action="version", version=f"centric-api {__version__}")
    parser.add_argument(
        "--config",
        help="Config file (default: $CENTRIC_API_CONFIG, then ./centric-api.yml).",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    fetch = commands.add_parser("fetch", help="Fetch records into bronze and update silver.")
    fetch.add_argument(
        "--full",
        action="store_true",
        help="Fetch every record (add --allow-delete to flag deleted records).",
    )
    fetch.add_argument(
        "-e",
        "--endpoint",
        action="append",
        dest="endpoints",
        metavar="NAME",
        help="Only this endpoint (repeatable).",
    )
    fetch.add_argument(
        "--restart", action="store_true", help="Ignore unfinished fetches instead of resuming."
    )
    fetch.add_argument(
        "--allow-delete",
        action="store_true",
        help="With --full: flag records that no longer exist in Centric as deleted.",
    )
    _output_flags(fetch)
    fetch.set_defaults(handler=_fetch)

    swagger = commands.add_parser(
        "swagger", help="Refresh the stored API specs and update silver tables."
    )
    _output_flags(swagger)
    swagger.set_defaults(handler=_swagger)

    silver = commands.add_parser(
        "silver", help="Update silver tables from bronze without calling the API."
    )
    silver.add_argument("--rebuild", action="store_true", help="Rebuild from scratch.")
    silver.add_argument(
        "-e",
        "--endpoint",
        action="append",
        dest="endpoints",
        metavar="NAME",
        help="Only this endpoint (repeatable).",
    )
    _output_flags(silver)
    silver.set_defaults(handler=_silver)

    status = commands.add_parser("status", help="Show what is stored and when it was fetched.")
    status.add_argument("--json", action="store_true", help="Print JSON.")
    status.set_defaults(handler=_status)

    init = commands.add_parser("init", help="Write a starter centric-api.yml.")
    init.add_argument("path", nargs="?", default="centric-api.yml")
    init.set_defaults(handler=_init)
    return parser


def _output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print a JSON summary on stdout.")
    parser.add_argument("-q", "--quiet", action="store_true", help="No progress output.")


def _progress(args: argparse.Namespace):
    if args.quiet:
        return None
    return lambda message: print(message, file=sys.stderr, flush=True)


def _fetch(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    summary = run_fetch(
        settings,
        full=args.full,
        endpoint_names=args.endpoints,
        restart=args.restart,
        allow_delete=args.allow_delete,
        progress=_progress(args),
    )
    if args.json:
        print(json.dumps(_summary_json(summary), indent=2))
    elif not args.quiet:
        _print_summary(summary)
    return 0 if summary.status in {"ok", "warn"} else 1


def _swagger(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    refreshed, silver = run_swagger(settings)
    failed = [item for item in refreshed if item.status == "failed"]
    if args.json:
        print(
            json.dumps(
                {"swagger": [asdict(item) for item in refreshed], "silver": _silver_json(silver)},
                indent=2,
            )
        )
    elif not args.quiet:
        for item in refreshed:
            print(f"swagger {item.api_version}: {item.status}" + _note(item.message))
        _print_silver(silver)
    return 1 if failed and len(failed) == len(refreshed) else 0


def _silver(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    results = run_silver(settings, endpoint_names=args.endpoints, rebuild=args.rebuild)
    if args.json:
        print(json.dumps(_silver_json(results), indent=2))
    elif not args.quiet:
        _print_silver(results)
    return 0


def _status(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    status = collect_status(settings)
    if args.json:
        print(json.dumps(status, indent=2))
        return 0
    print(f"Database: {status['database']}" + ("" if status["exists"] else " (not created yet)"))
    last_run = status["last_run"]
    if last_run:
        print(
            f"Last run: {last_run['command']} {last_run['status']} "
            f"(started {last_run['started_at']})" + _note(last_run["error"])
        )
    if status["lock"]:
        print(f"Lock: held by {status['lock']['owner']} since {status['lock']['heartbeat_at']}")
    for item in status["swagger"]:
        print(f"Swagger {item['api_version']}: fetched {item['fetched_at']}")
    print()
    header = f"{'endpoint':30} {'active':>10} {'deleted':>8}  {'last success':24} state"
    print(header)
    for row in status["endpoints"]:
        state = row["last_attempt_status"] or "never fetched"
        if row["pending_resume"]:
            state += " (resume pending)"
        if row["unmapped_fields"]:
            state += f" unmapped={','.join(row['unmapped_fields'])}"
        print(
            f"{row['endpoint']:30} {row['active_records']:>10,} {row['deleted_records']:>8,}  "
            f"{row['last_success_at'] or '-':24} {state}"
        )
    return 0


def _init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists():
        print(f"Error: {path} already exists.", file=sys.stderr)
        return 2
    text = resources.files("centric_api").joinpath("centric-api.example.yml").read_text("utf-8")
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}. Put credentials in .env next to it (see the README).")
    return 0


def _print_summary(summary: FetchSummary) -> None:
    print()
    print(f"Fetch {summary.status} (run {summary.run_id})" + _note(summary.error))
    for item in summary.endpoints:
        counts = item.counts
        line = (
            f"  {item.endpoint:30} {item.status:6} {item.mode:5} fetched={item.fetched:,} "
            f"new={counts.inserted:,} changed={counts.updated:,} deleted={item.deleted:,}"
        )
        if item.missing:
            line += f" missing={item.missing:,}"
        if item.error:
            line += f"  error: {item.error}"
        print(line)
    unmapped = [result for result in summary.silver if result.unmapped]
    if unmapped:
        print("Fields in payloads but not in the spec (kept in bronze only):")
        for result in unmapped:
            print(f"  {result.endpoint}: {', '.join(result.unmapped)}")


def _print_silver(results: list[SilverResult]) -> None:
    for result in results:
        line = f"silver {result.endpoint}: {result.status}"
        if result.status != "skipped":
            line += f" rows={result.rows:,}"
        if result.unmapped:
            line += f" unmapped={','.join(result.unmapped)}"
        for note in result.notes:
            line += f" ({note})"
        print(line)


def _summary_json(summary: FetchSummary) -> dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "status": summary.status,
        "error": summary.error,
        "swagger": [asdict(item) for item in summary.swagger],
        "endpoints": [asdict(item) for item in summary.endpoints],
        "silver": _silver_json(summary.silver),
    }


def _silver_json(results: list[SilverResult]) -> list[dict[str, Any]]:
    return [asdict(result) for result in results]


def _note(message: str | None) -> str:
    return f" ({message})" if message else ""
