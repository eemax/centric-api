from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from .client import CentricClient, load_credentials
from .config import EndpointSpec, Settings
from .db import RunLock, connect, table_exists, transaction
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
