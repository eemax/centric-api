from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..db_schema import ensure_changelog_read_indexes
from ..store import connect_readonly, table_exists
from .models import ACTIVITY_AT_SQL, DELETE_TYPE_HARD_DELETE, DELETE_TYPE_TOMBSTONE
from .utils import _datetime_to_db, _json_dict


def list_changelog_runs(
    db_path: Path,
    *,
    since: datetime | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    clause, params = _since_filter(since, "created_at")
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_changelog_runs"):
            return []
        rows = conn.execute(
            f"""
            SELECT run_id, created_at, endpoint_count, record_count, event_count,
                   full_refresh, scoped_record_count
            FROM endpoint_changelog_runs
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def ensure_changelog_read_schema(
    db_path: Path,
    *,
    include_field_indexes: bool = False,
) -> None:
    if not db_path.is_file():
        return
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        if table_exists(conn, "endpoint_change_events") or table_exists(
            conn,
            "endpoint_changelog_runs",
        ):
            ensure_changelog_read_indexes(conn, include_field_indexes=include_field_indexes)


def _try_ensure_changelog_read_schema(
    db_path: Path,
    *,
    include_field_indexes: bool = False,
) -> None:
    try:
        ensure_changelog_read_schema(db_path, include_field_indexes=include_field_indexes)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise


def list_change_summary(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    if since is not None:
        return _list_change_summary_by_activity(
            db_path,
            endpoint=endpoint,
            since=since,
            limit=limit,
        )
    clauses: list[str] = []
    params: list[Any] = []
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    if since is not None:
        clauses.append(f"{ACTIVITY_AT_SQL} >= ?")
        params.append(_datetime_to_db(since))
    clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_summary"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, change_type, delete_type, SUM(count) AS count
            FROM endpoint_change_summary
            {clause}
            GROUP BY endpoint, change_type, delete_type
            ORDER BY endpoint, change_type, delete_type
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def _list_change_summary_by_activity(
    db_path: Path,
    *,
    endpoint: str | None,
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    ensure_changelog_read_schema(db_path)
    clauses, params = _activity_clauses(endpoint=endpoint, since=since)
    clause = "WHERE " + " AND ".join(clauses)
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_events"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, change_type, delete_type, COUNT(*) AS count
            FROM endpoint_change_events INDEXED BY idx_endpoint_change_events_activity_at
            {clause}
            GROUP BY endpoint, change_type, delete_type
            ORDER BY endpoint, change_type, delete_type
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def list_actor_totals(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    if since is not None:
        return _list_actor_totals_by_activity(
            db_path,
            endpoint=endpoint,
            since=since,
            limit=limit,
        )
    clauses: list[str] = []
    params: list[Any] = []
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    if since is not None:
        clauses.append(f"{ACTIVITY_AT_SQL} >= ?")
        params.append(_datetime_to_db(since))
    clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_actor_change_summary"):
            return []
        rows = conn.execute(
            f"""
            SELECT modified_by_id, modified_by_name, SUM(count) AS count
            FROM endpoint_actor_change_summary
            {clause}
            GROUP BY modified_by_id, modified_by_name
            ORDER BY count DESC, modified_by_name, modified_by_id
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def _list_actor_totals_by_activity(
    db_path: Path,
    *,
    endpoint: str | None,
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    ensure_changelog_read_schema(db_path)
    clauses, params = _activity_clauses(endpoint=endpoint, since=since)
    clause = "WHERE " + " AND ".join(clauses)
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_events"):
            return []
        rows = conn.execute(
            f"""
            SELECT modified_by_id, modified_by_name, COUNT(*) AS count
            FROM endpoint_change_events INDEXED BY idx_endpoint_change_events_activity_at
            {clause}
            GROUP BY modified_by_id, modified_by_name
            ORDER BY count DESC, modified_by_name, modified_by_id
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def list_field_summary(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    if since is not None:
        return _list_field_summary_by_activity(
            db_path,
            endpoint=endpoint,
            since=since,
            limit=limit,
        )
    clauses: list[str] = []
    params: list[Any] = []
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    if since is not None:
        clauses.append("changed_at >= ?")
        params.append(_datetime_to_db(since))
    clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_field_change_summary"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, field, field_change_type, event_change_type, SUM(count) AS count
            FROM endpoint_field_change_summary
            {clause}
            GROUP BY endpoint, field, field_change_type, event_change_type
            ORDER BY count DESC, endpoint, field, field_change_type, event_change_type
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def _list_field_summary_by_activity(
    db_path: Path,
    *,
    endpoint: str | None,
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    ensure_changelog_read_schema(db_path, include_field_indexes=True)
    clauses = ["COALESCE(e.modified_at, e.changed_at) >= ?"]
    params: list[Any] = [_datetime_to_db(since)]
    if endpoint:
        clauses.insert(0, "e.endpoint = ?")
        params.insert(0, endpoint)
    clause = "WHERE " + " AND ".join(clauses)
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_events") or not table_exists(
            conn,
            "endpoint_change_fields",
        ):
            return []
        rows = conn.execute(
            f"""
            SELECT f.endpoint, f.field, f.field_change_type, f.event_change_type,
                   COUNT(*) AS count
            FROM endpoint_change_events AS e INDEXED BY idx_endpoint_change_events_activity_at
            JOIN endpoint_change_fields f ON f.event_id = e.id
            {clause}
            GROUP BY f.endpoint, f.field, f.field_change_type, f.event_change_type
            ORDER BY count DESC, f.endpoint, f.field, f.field_change_type, f.event_change_type
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def list_actor_summary(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    if since is not None:
        return _list_actor_summary_by_activity(
            db_path,
            endpoint=endpoint,
            since=since,
            limit=limit,
        )
    clauses: list[str] = []
    params: list[Any] = []
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    if since is not None:
        clauses.append("changed_at >= ?")
        params.append(_datetime_to_db(since))
    clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_actor_change_summary"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, modified_by_id, modified_by_name, change_type,
                   delete_type, SUM(count) AS count
            FROM endpoint_actor_change_summary
            {clause}
            GROUP BY endpoint, modified_by_id, modified_by_name, change_type, delete_type
            ORDER BY count DESC, endpoint, modified_by_name, modified_by_id, change_type
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def _list_actor_summary_by_activity(
    db_path: Path,
    *,
    endpoint: str | None,
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    ensure_changelog_read_schema(db_path)
    clauses, params = _activity_clauses(endpoint=endpoint, since=since)
    clause = "WHERE " + " AND ".join(clauses)
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_events"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, modified_by_id, modified_by_name, change_type,
                   delete_type, COUNT(*) AS count
            FROM endpoint_change_events INDEXED BY idx_endpoint_change_events_activity_at
            {clause}
            GROUP BY endpoint, modified_by_id, modified_by_name, change_type, delete_type
            ORDER BY count DESC, endpoint, modified_by_name, modified_by_id, change_type
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def list_actor_leaderboard(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    if since is not None:
        return _list_actor_leaderboard_by_activity(
            db_path,
            endpoint=endpoint,
            since=since,
        )
    clauses: list[str] = []
    params: list[Any] = []
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    if since is not None:
        clauses.append("changed_at >= ?")
        params.append(_datetime_to_db(since))
    clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_actor_change_summary"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, modified_by_id, modified_by_name, change_type, delete_type,
                   SUM(count) AS count
            FROM endpoint_actor_change_summary
            {clause}
            GROUP BY endpoint, modified_by_id, modified_by_name, change_type, delete_type
            """,
            params,
        ).fetchall()
    return _actor_leaderboard_rows([dict(row) for row in rows])


def _list_actor_leaderboard_by_activity(
    db_path: Path,
    *,
    endpoint: str | None,
    since: datetime,
) -> list[dict[str, Any]]:
    ensure_changelog_read_schema(db_path)
    clauses, params = _activity_clauses(endpoint=endpoint, since=since)
    clause = "WHERE " + " AND ".join(clauses)
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_events"):
            return []
        rows = conn.execute(
            f"""
            SELECT endpoint, modified_by_id, modified_by_name, change_type, delete_type,
                   COUNT(*) AS count
            FROM endpoint_change_events INDEXED BY idx_endpoint_change_events_activity_at
            {clause}
            GROUP BY endpoint, modified_by_id, modified_by_name, change_type, delete_type
            """,
            params,
        ).fetchall()
    return _actor_leaderboard_rows([dict(row) for row in rows])


def list_changes(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
    include_payloads: bool = True,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    _try_ensure_changelog_read_schema(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    if since is not None:
        clauses.append(f"{ACTIVITY_AT_SQL} >= ?")
        params.append(_datetime_to_db(since))
    clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    payload_columns = ""
    if include_payloads:
        payload_columns = ", previous_payload_json, current_payload_json"
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_change_events"):
            return []
        table_ref = _change_events_table_ref(conn, endpoint=endpoint)
        rows = conn.execute(
            f"""
            SELECT run_id, endpoint, record_id, changed_at, change_type,
                   delete_type, modified_at, modified_by_id, modified_by_name,
                   changed_fields_json{payload_columns}
            FROM {table_ref}
            {clause}
            ORDER BY COALESCE(modified_at, changed_at) DESC, changed_at DESC, endpoint, record_id
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [
        {
            **dict(row),
            "changed_fields": json.loads(row["changed_fields_json"]),
            **_change_payloads(row, include_payloads=include_payloads),
        }
        for row in rows
    ]


def _change_payloads(row: sqlite3.Row, *, include_payloads: bool) -> dict[str, Any]:
    if not include_payloads:
        return {}
    return {
        "previous_payload": _json_dict(row["previous_payload_json"]),
        "current_payload": _json_dict(row["current_payload_json"]),
    }


def _change_events_table_ref(conn: sqlite3.Connection, *, endpoint: str | None) -> str:
    if endpoint and _index_exists(conn, "idx_endpoint_change_events_endpoint_activity_sort"):
        return "endpoint_change_events INDEXED BY idx_endpoint_change_events_endpoint_activity_sort"
    if _index_exists(conn, "idx_endpoint_change_events_activity_sort"):
        return "endpoint_change_events INDEXED BY idx_endpoint_change_events_activity_sort"
    if _index_exists(conn, "idx_endpoint_change_events_activity_at"):
        return "endpoint_change_events INDEXED BY idx_endpoint_change_events_activity_at"
    return "endpoint_change_events"


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'index'
              AND name = ?
            """,
            [name],
        ).fetchone()
        is not None
    )


def _actor_leaderboard_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actors: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for row in rows:
        actor_key = (row.get("modified_by_id"), row.get("modified_by_name"))
        actor_row = actors.setdefault(
            actor_key,
            {
                "modified_by_id": actor_key[0],
                "modified_by_name": actor_key[1],
                "total": 0,
                "added": 0,
                "changed": 0,
                "removed": 0,
                "tombstone": 0,
                "hard_delete": 0,
                "unknown_delete": 0,
                "endpoints": {},
            },
        )
        endpoint = str(row["endpoint"])
        endpoint_rows = actor_row["endpoints"]
        endpoint_row = endpoint_rows.setdefault(
            endpoint,
            {
                "endpoint": endpoint,
                "total": 0,
                "added": 0,
                "changed": 0,
                "removed": 0,
                "tombstone": 0,
                "hard_delete": 0,
                "unknown_delete": 0,
            },
        )
        count = int(row["count"] or 0)
        change_type = row["change_type"]
        if change_type == "added":
            actor_row["added"] += count
            endpoint_row["added"] += count
        elif change_type == "changed":
            actor_row["changed"] += count
            endpoint_row["changed"] += count
        elif change_type == "removed":
            actor_row["removed"] += count
            endpoint_row["removed"] += count
            delete_type = row.get("delete_type")
            if delete_type == DELETE_TYPE_TOMBSTONE:
                actor_row["tombstone"] += count
                endpoint_row["tombstone"] += count
            elif delete_type == DELETE_TYPE_HARD_DELETE:
                actor_row["hard_delete"] += count
                endpoint_row["hard_delete"] += count
            else:
                actor_row["unknown_delete"] += count
                endpoint_row["unknown_delete"] += count
        actor_row["total"] += count
        endpoint_row["total"] += count

    leaderboard_rows: list[dict[str, Any]] = []
    for row in actors.values():
        endpoints = sorted(
            row["endpoints"].values(),
            key=lambda endpoint_row: (-int(endpoint_row["total"]), endpoint_row["endpoint"]),
        )
        leaderboard_rows.append({**row, "endpoints": endpoints})
    return sorted(
        leaderboard_rows,
        key=lambda row: (
            -int(row["total"]),
            _actor_sort_label(row),
            row.get("modified_by_id") or "",
        ),
    )


def _actor_sort_label(row: dict[str, Any]) -> str:
    return str(row.get("modified_by_name") or row.get("modified_by_id") or "Unknown")


def parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    unit = text[-1].lower()
    amount_text = text[:-1]
    if unit in {"h", "d", "m"} and amount_text.isdigit():
        amount = int(amount_text)
        if unit == "h":
            return datetime.now(UTC) - timedelta(hours=amount)
        if unit == "d":
            return datetime.now(UTC) - timedelta(days=amount)
        return datetime.now(UTC) - timedelta(minutes=amount)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _since_filter(since: datetime | None, column: str) -> tuple[str, list[Any]]:
    if since is None:
        return "", []
    return f"WHERE {column} >= ?", [_datetime_to_db(since)]


def _activity_clauses(
    *,
    endpoint: str | None,
    since: datetime,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = [f"{ACTIVITY_AT_SQL} >= ?"]
    params: list[Any] = [_datetime_to_db(since)]
    if endpoint:
        clauses.insert(0, "endpoint = ?")
        params.insert(0, endpoint)
    return clauses, params
