from __future__ import annotations

import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .client import CentricClient, load_credentials
from .config import ConfigError, EndpointSpec, Settings
from .db import (
    LOCK_STALE_AFTER,
    DatabaseError,
    RunLock,
    _owner_is_dead_local_process,
    connect,
    iso,
    table_exists,
    transaction,
    utc_now,
)
from .fetch import select_endpoints
from .silver import SilverResult, main_table, sync_endpoint
from .swagger import SwaggerRefresh, load_specs, refresh_specs


def run_swagger(
    settings: Settings, *, client: CentricClient | None = None
) -> tuple[list[SwaggerRefresh], list[SilverResult]]:
    """Refresh the stored specs, then bring every endpoint's silver tables up to date."""
    conn = connect(settings.database)
    try:
        with RunLock(conn) as lock:
            owned = client is None
            client = client or CentricClient(load_credentials(settings.env_file), settings.http)
            try:
                refreshed = refresh_specs(
                    conn, client, {spec.api_version for spec in settings.endpoints}
                )
            finally:
                if owned:
                    client.close()
            return refreshed, _sync_all(conn, lock, settings.endpoints)
    finally:
        conn.close()


def run_silver(
    settings: Settings, *, endpoint_names: list[str] | None = None, rebuild: bool = False
) -> list[SilverResult]:
    """Sync (or rebuild) silver from bronze using the stored specs. No network access."""
    selected = select_endpoints(settings.endpoints, endpoint_names)
    conn = connect(settings.database)
    try:
        with RunLock(conn) as lock:
            return _sync_all(conn, lock, selected, rebuild=rebuild)
    finally:
        conn.close()


def _sync_all(
    conn: sqlite3.Connection,
    lock: RunLock,
    endpoints: Iterable[EndpointSpec],
    *,
    rebuild: bool = False,
) -> list[SilverResult]:
    specs = load_specs(conn)
    results = []
    for spec in endpoints:
        with transaction(conn):
            lock.heartbeat()
        results.append(sync_endpoint(conn, spec, specs, force_rebuild=rebuild))
    return results


def collect_status(settings: Settings) -> dict[str, Any]:
    if not settings.database.exists():
        return {
            "database": str(settings.database),
            "exists": False,
            "last_run": None,
            "lock": None,
            "swagger": [],
            "endpoints": [_empty_endpoint_status(spec.name) for spec in settings.endpoints],
        }
    conn = connect(settings.database, readonly=True)
    try:
        bronze = {
            row["endpoint"]: row
            for row in conn.execute(
                """
                SELECT endpoint, SUM(deleted = 0) AS active, SUM(deleted = 1) AS deleted
                FROM bronze_records GROUP BY endpoint
                """
            )
        }
        states = {row["endpoint"]: row for row in conn.execute("SELECT * FROM meta_endpoint_state")}
        silver_states = {
            row["endpoint"]: row for row in conn.execute("SELECT * FROM meta_silver_state")
        }
        unmapped: dict[str, list[str]] = {}
        for row in conn.execute(
            "SELECT endpoint, field FROM meta_unmapped_fields ORDER BY endpoint, field"
        ):
            unmapped.setdefault(row["endpoint"], []).append(row["field"])
        endpoints = []
        for spec in settings.endpoints:
            state = states.get(spec.name)
            counts = bronze.get(spec.name)
            silver = silver_states.get(spec.name)
            endpoints.append(
                {
                    "endpoint": spec.name,
                    "active_records": int(counts["active"]) if counts else 0,
                    "deleted_records": int(counts["deleted"]) if counts else 0,
                    "last_success_at": state["last_success_finished_at"] if state else None,
                    "incremental_cursor": state["last_success_started_at"] if state else None,
                    "last_success_mode": state["last_success_mode"] if state else None,
                    "last_full_success_at": state["last_full_success_at"] if state else None,
                    "last_attempt_status": state["last_attempt_status"] if state else None,
                    "last_attempt_error": state["last_attempt_error"] if state else None,
                    "pending_resume": bool(state and state["pending_run_id"]),
                    "silver_table": main_table(spec.name)
                    if table_exists(conn, main_table(spec.name))
                    else None,
                    "silver_synced_at": silver["synced_at"] if silver else None,
                    "unmapped_fields": unmapped.get(spec.name, []),
                }
            )
        swagger = [
            {"api_version": row["api_version"], "fetched_at": row["fetched_at"]}
            for row in conn.execute("SELECT api_version, fetched_at FROM meta_swagger")
        ]
        last_run = conn.execute(
            "SELECT run_id, command, started_at, finished_at, status, error FROM meta_runs "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        lock = conn.execute("SELECT owner, heartbeat_at FROM meta_lock").fetchone()
        return {
            "database": str(settings.database),
            "exists": True,
            "last_run": dict(last_run) if last_run else None,
            "lock": dict(lock) if lock else None,
            "swagger": swagger,
            "endpoints": endpoints,
        }
    finally:
        conn.close()


def _empty_endpoint_status(endpoint: str) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "active_records": 0,
        "deleted_records": 0,
        "last_success_at": None,
        "incremental_cursor": None,
        "last_success_mode": None,
        "last_full_success_at": None,
        "last_attempt_status": None,
        "last_attempt_error": None,
        "pending_resume": False,
        "silver_table": None,
        "silver_synced_at": None,
        "unmapped_fields": [],
    }


# ---------------------------------------------------------------------------- check


def parse_duration(text: str) -> timedelta:
    """'90' (seconds), '45m', '26h', '2d' -> timedelta."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(text))
    if not match:
        raise ConfigError(f"Invalid duration {text!r}; use e.g. 90, 45m, 26h or 2d.")
    value, unit = float(match.group(1)), match.group(2) or "s"
    seconds = value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    if seconds <= 0:
        raise ConfigError("Duration must be positive.")
    return timedelta(seconds=seconds)


def collect_health(
    settings: Settings,
    *,
    max_age: timedelta | None = None,
    endpoint_names: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Is the data fresh and the last fetch good? For monitors and schedulers.

    Healthy means: the database exists; the most recent fetch run didn't fail (a run in
    progress counts as fine while its lock is alive); every selected endpoint has a
    successful fetch, its last attempt didn't fail, and with ``max_age`` that success is
    recent enough; and no abandoned lock is left behind.

    Reads only the small bookkeeping tables, never the records, so it stays cheap enough
    to run every minute on a large database.
    """
    selected = select_endpoints(settings.endpoints, endpoint_names)
    now = now or utc_now()
    if not settings.database.exists():
        return {
            "healthy": False,
            "checked_at": iso(now),
            "max_age_seconds": round(max_age.total_seconds()) if max_age else None,
            "problems": [f"database not created yet: {settings.database}"],
            "last_fetch": None,
            "running": False,
            "endpoints": [],
        }
    conn = connect(settings.database, readonly=True)
    try:
        last_fetch = conn.execute(
            "SELECT run_id, command, started_at, finished_at, status, error FROM meta_runs "
            "WHERE command LIKE 'fetch%' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        lock = conn.execute("SELECT owner, heartbeat_at FROM meta_lock").fetchone()
        states = {
            row["endpoint"]: row
            for row in conn.execute(
                "SELECT endpoint, last_success_finished_at, last_attempt_status, "
                "last_attempt_error FROM meta_endpoint_state"
            )
        }
    finally:
        conn.close()
    problems: list[str] = []
    lock_alive = (
        lock is not None
        and now - _parse_iso(lock["heartbeat_at"]) < LOCK_STALE_AFTER
        and not _owner_is_dead_local_process(lock["owner"])
    )
    if lock is not None and not lock_alive:
        problems.append(
            f"abandoned lock from {lock['owner']} (last heartbeat {lock['heartbeat_at']})"
        )
    if last_fetch is None:
        problems.append("no fetch has run yet")
    elif last_fetch["status"] == "running":
        if not lock_alive:
            problems.append(
                f"fetch {last_fetch['run_id']} never finished (started {last_fetch['started_at']})"
            )
    elif last_fetch["status"] not in {"ok", "warn"}:
        problems.append(
            f"last fetch {last_fetch['status']} (started {last_fetch['started_at']})"
            + (f": {last_fetch['error']}" if last_fetch["error"] else "")
        )
    endpoints = []
    for spec in selected:
        state = states.get(spec.name)
        last_success = state["last_success_finished_at"] if state else None
        age = (now - _parse_iso(last_success)).total_seconds() if last_success else None
        endpoint_problems = []
        if not last_success:
            endpoint_problems.append("never fetched successfully")
        elif max_age is not None and age is not None and age > max_age.total_seconds():
            endpoint_problems.append(f"last success {last_success} is older than {_human(max_age)}")
        if state and state["last_attempt_status"] == "failed":
            endpoint_problems.append(
                "last attempt failed"
                + (f": {state['last_attempt_error']}" if state["last_attempt_error"] else "")
            )
        problems.extend(f"{spec.name}: {text}" for text in endpoint_problems)
        endpoints.append(
            {
                "endpoint": spec.name,
                "healthy": not endpoint_problems,
                "last_success_at": last_success,
                "age_seconds": round(age) if age is not None else None,
                "last_attempt_status": state["last_attempt_status"] if state else None,
                "problems": endpoint_problems,
            }
        )
    return {
        "healthy": not problems,
        "checked_at": iso(now),
        "max_age_seconds": round(max_age.total_seconds()) if max_age else None,
        "problems": problems,
        "last_fetch": dict(last_fetch) if last_fetch else None,
        "running": lock_alive,
        "endpoints": endpoints,
    }


def _human(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0 and seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ------------------------------------------------------------------------- maintain


@dataclass
class MaintainResult:
    database: str
    size_before: int
    size_after: int
    wal_before: int
    wal_after: int
    integrity: str
    vacuumed: bool
    seconds: float
    notes: list[str] = field(default_factory=list)


def run_maintain(
    settings: Settings, *, vacuum: bool = False, emit: Callable[[str], None] | None = None
) -> MaintainResult:
    """Database upkeep: quick integrity check, WAL checkpoint, ANALYZE via PRAGMA optimize,
    and with ``vacuum`` a VACUUM that returns free pages to the filesystem.

    Takes the writer lock, so it never runs during a fetch, and refreshes its heartbeat
    between steps. VACUUM writes a full temporary copy and then the new contents through
    the WAL, so it needs about twice the database's size free; it's skipped (with a note)
    when that isn't there.
    """
    say = emit or (lambda _message: None)
    path = settings.database
    if not path.is_file():
        raise DatabaseError(f"Database not found: {path}")
    started = time.monotonic()
    wal = path.with_name(path.name + "-wal")
    size_before = path.stat().st_size
    wal_before = wal.stat().st_size if wal.exists() else 0
    notes: list[str] = []
    conn = connect(path)
    try:
        with RunLock(conn) as lock:

            def heartbeat() -> None:
                with transaction(conn):
                    lock.heartbeat()

            say("integrity: quick check")
            rows = [row[0] for row in conn.execute("PRAGMA quick_check")]
            integrity = "ok" if rows == ["ok"] else "; ".join(rows[:5])
            if integrity != "ok":
                raise DatabaseError(f"integrity check failed: {integrity}")
            heartbeat()
            did_vacuum = False
            if vacuum:
                needed = int(size_before * 2.1)
                free = shutil.disk_usage(path.parent).free
                if free < needed:
                    notes.append(f"vacuum skipped: needs ~{needed:,} bytes free, have {free:,}")
                else:
                    say("vacuum: rewriting the database file")
                    conn.execute("VACUUM")
                    did_vacuum = True
                heartbeat()
            say("optimize: refreshing query planner statistics")
            conn.execute("PRAGMA optimize")
            say("checkpoint: folding the WAL into the database")
            busy, _log, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy:
                notes.append("checkpoint incomplete: a reader was active; the next one finishes it")
    finally:
        conn.close()
    return MaintainResult(
        database=str(path),
        size_before=size_before,
        size_after=path.stat().st_size,
        wal_before=wal_before,
        wal_after=wal.stat().st_size if wal.exists() else 0,
        integrity=integrity,
        vacuumed=did_vacuum,
        seconds=round(time.monotonic() - started, 1),
        notes=notes,
    )
