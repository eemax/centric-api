from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__
from .client import AuthError, FetchError
from .config import ConfigError, load_settings
from .db import DatabaseError, LockError
from .fetch import FetchSummary, run_fetch, select_endpoints
from .maintenance import (
    collect_health,
    collect_status,
    parse_duration,
    run_maintain,
    run_silver,
    run_swagger,
)
from .read import Reader
from .silver import SilverResult


class Interrupted(KeyboardInterrupt):
    """A stop request by signal. Subclasses KeyboardInterrupt so every cleanup path that
    handles Ctrl-C (transaction rollback, lock release) handles it the same way."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


# Signals that ask a run to stop: Ctrl-C, systemd/kill/shutdown, a closed terminal.
STOP_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)


def _request_stop(signum: int, _frame: object) -> None:
    # Ignore repeats so a second signal can't interrupt the cleanup (rollback, lock
    # release) that this one starts. SIGKILL remains the way to stop it hard.
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    raise Interrupted(signum)


@contextmanager
def _stop_signals_interrupt() -> Iterator[None]:
    """Turn SIGTERM/SIGHUP into the same clean stop as Ctrl-C for the duration of a
    command, then restore whatever handlers were there before."""
    previous = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
    for sig in STOP_SIGNALS:
        signal.signal(sig, _request_stop)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        with _stop_signals_interrupt():
            return args.handler(args)
    except (ConfigError, AuthError, FetchError, LockError, DatabaseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Error: database: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt as exc:
        signum = exc.signum if isinstance(exc, Interrupted) else signal.SIGINT
        name = signal.Signals(signum).name
        print(
            f"Interrupted ({name}). The next fetch resumes where this one stopped.",
            file=sys.stderr,
        )
        # Conventional shell status for "terminated by signal N": 130 for SIGINT,
        # 143 for SIGTERM, 129 for SIGHUP. Never 0: an interrupted run didn't finish.
        return 128 + signum


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
    fetch.add_argument(
        "--log-format",
        choices=("text", "logfmt", "json"),
        default="text",
        help="Progress on stderr: text (default, human), or one logfmt/JSON line per "
        "finished endpoint plus one for the run (for journald and log shippers).",
    )
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

    check = commands.add_parser(
        "check",
        help="Exit 0 if the last fetch is good and the data fresh, 1 (with reasons) if not.",
    )
    check.add_argument(
        "--max-age",
        metavar="DURATION",
        help="Also require every endpoint's last success to be newer than this "
        "(e.g. 26h, 90m, 2d).",
    )
    check.add_argument(
        "-e",
        "--endpoint",
        action="append",
        dest="endpoints",
        metavar="NAME",
        help="Only check this endpoint (repeatable).",
    )
    check.add_argument("--json", action="store_true", help="Print JSON.")
    check.add_argument("-q", "--quiet", action="store_true", help="No output; exit status only.")
    check.set_defaults(handler=_check)

    changes = commands.add_parser(
        "changes",
        help="Records changed after a change sequence number (default: all), per endpoint.",
    )
    changes.add_argument(
        "--since",
        type=int,
        default=0,
        metavar="SEQ",
        help="Change sequence number from an earlier `changes` or fetch summary.",
    )
    changes.add_argument(
        "-e",
        "--endpoint",
        action="append",
        dest="endpoints",
        metavar="NAME",
        help="Only this endpoint (repeatable).",
    )
    changes.add_argument("--json", action="store_true", help="Print JSON.")
    changes.set_defaults(handler=_changes)

    maintain = commands.add_parser(
        "maintain",
        help="Database upkeep: integrity check, WAL checkpoint, optimize (and --vacuum).",
    )
    maintain.add_argument(
        "--vacuum",
        action="store_true",
        help="Also VACUUM: reclaim free space (needs about the database's size free on disk).",
    )
    _output_flags(maintain)
    maintain.set_defaults(handler=_maintain)

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
    structured = args.log_format != "text" and not args.quiet
    summary = run_fetch(
        settings,
        full=args.full,
        endpoint_names=args.endpoints,
        restart=args.restart,
        allow_delete=args.allow_delete,
        progress=None if structured else _progress(args),
        on_event=_event_printer(args.log_format) if structured else None,
    )
    if structured and not args.json:
        return 0 if summary.status in {"ok", "warn"} else 1
    if args.json:
        print(json.dumps(_summary_json(summary), indent=2))
    elif not args.quiet:
        _print_summary(summary)
    return 0 if summary.status in {"ok", "warn"} else 1


def _event_printer(log_format: str):
    def emit(event: dict[str, Any]) -> None:
        if log_format == "json":
            line = json.dumps(event, separators=(",", ":"))
        else:
            line = " ".join(f"{key}={_logfmt(value)}" for key, value in event.items())
        print(line, file=sys.stderr, flush=True)

    return emit


def _logfmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # Quote anything that would split or break the line: spaces, newlines, tabs, = and ".
    if text == "" or any(ch.isspace() or ch in '"=\\' for ch in text):
        return json.dumps(text)
    return text


def _check(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    max_age = parse_duration(args.max_age) if args.max_age else None
    health = collect_health(settings, max_age=max_age, endpoint_names=args.endpoints)
    if args.json:
        print(json.dumps(health, indent=2))
    elif not args.quiet:
        if health["healthy"]:
            fetch = health["last_fetch"] or {}
            extra = " (a centric-api run is in progress)" if health["running"] else ""
            print(f"healthy: last fetch {fetch.get('status')} at {fetch.get('finished_at')}{extra}")
        else:
            print("unhealthy:")
            for problem in health["problems"]:
                print(f"  {problem}")
    return 0 if health["healthy"] else 1


def _changes(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if not settings.database.is_file():
        raise DatabaseError(f"Database not found: {settings.database}")
    names = [spec.name for spec in select_endpoints(settings.endpoints, args.endpoints)]
    with Reader(settings.database) as reader:
        rows = reader.changes(since=args.since, endpoints=names)
        seq = reader.change_seq()
    total = sum(row.total for row in rows)
    if args.json:
        print(
            json.dumps(
                {
                    "since": args.since,
                    "change_seq": seq,
                    "total": total,
                    "endpoints": [{**asdict(row), "total": row.total} for row in rows],
                },
                indent=2,
            )
        )
        return 0
    print(f"change_seq: {seq} (since {args.since}: {total:,} records changed)")
    for row in rows:
        if row.total:
            print(f"  {row.endpoint:30} changed={row.changed:,} deleted={row.deleted:,}")
    return 0


def _maintain(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    result = run_maintain(settings, vacuum=args.vacuum, emit=_progress(args))
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    elif not args.quiet:
        mb = 1024 * 1024
        print(
            f"maintain: integrity {result.integrity}, "
            f"database {result.size_before / mb:,.0f} -> {result.size_after / mb:,.0f} MiB, "
            f"wal {result.wal_before / mb:,.0f} -> {result.wal_after / mb:,.0f} MiB"
            + (", vacuumed" if result.vacuumed else "")
            + f" ({result.seconds}s)"
        )
        for note in result.notes:
            print(f"  {note}")
    return 0


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
        "change_seq": {"before": summary.change_seq_before, "after": summary.change_seq_after},
        "records_changed": summary.records_changed,
        "seconds": summary.seconds,
        "swagger": [asdict(item) for item in summary.swagger],
        "endpoints": [asdict(item) for item in summary.endpoints],
        "silver": _silver_json(summary.silver),
    }


def _silver_json(results: list[SilverResult]) -> list[dict[str, Any]]:
    return [asdict(result) for result in results]


def _note(message: str | None) -> str:
    return f" ({message})" if message else ""
