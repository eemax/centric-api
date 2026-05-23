from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .db_schema import ensure_changelog_tables
from .store import connect, connect_readonly, table_exists

CHANGELOG_SOURCE = "full-payload"
CHANGELOG_SOURCE_SHA = hashlib.sha256(CHANGELOG_SOURCE.encode("utf-8")).hexdigest()
MODIFIED_BY_FIELD = "modified_by"
MODIFIED_AT_FIELD = "_modified_at"
USER_ENDPOINT = "users"
USER_NAME_FIELD = "node_name"
DELETE_TYPE_TOMBSTONE = "tombstone"
DELETE_TYPE_HARD_DELETE = "hard_delete"
DELETE_TYPE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChangelogRun:
    run_id: str
    endpoint_count: int
    record_count: int
    event_count: int
    full_refresh: bool
    scoped_record_count: int


@dataclass(frozen=True)
class _IndexRow:
    endpoint: str
    record_id: str
    payload_hash: str
    payload_json: str


@dataclass(frozen=True)
class _ChangeEvent:
    run_id: str
    endpoint: str
    record_id: str
    changed_at: str
    change_type: str
    delete_type: str | None
    modified_at: str | None
    modified_by_id: str | None
    modified_by_name: str | None
    previous_hash: str | None
    current_hash: str | None
    changed_fields: list[str]
    previous_payload_json: str | None
    current_payload_json: str | None


def record_changelog(
    db_path: Path,
    *,
    endpoints: set[str] | None = None,
    record_ids_by_endpoint: dict[str, set[str]] | None = None,
    deleted_record_ids_by_endpoint: dict[str, set[str]] | None = None,
    deleted_record_delete_types_by_endpoint: dict[str, dict[str, str]] | None = None,
    full: bool = False,
) -> ChangelogRun:
    created_at = datetime.now(UTC)
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        run_id = _allocate_run_id(conn, created_at)
        has_record_scope = (
            record_ids_by_endpoint is not None or deleted_record_ids_by_endpoint is not None
        )
        scoped_keys = _scoped_record_keys(
            record_ids_by_endpoint=record_ids_by_endpoint or {},
            deleted_record_ids_by_endpoint=deleted_record_ids_by_endpoint or {},
        )
        endpoint_names = _resolve_endpoint_scope(
            conn,
            endpoints=endpoints,
            record_ids_by_endpoint=record_ids_by_endpoint,
            deleted_record_ids_by_endpoint=deleted_record_ids_by_endpoint,
        )
        full_refresh = (
            full
            or not has_record_scope
            or not _index_has_all_endpoints(
                conn,
                endpoint_names,
            )
        )

        if full_refresh:
            previous_index = _load_current_index(conn, endpoints=endpoint_names)
            current_index = _build_current_index(conn, endpoints=endpoint_names)
        else:
            previous_index = _load_current_index_for_keys(conn, keys=scoped_keys)
            current_index = _build_scoped_current_index(
                conn,
                record_ids_by_endpoint=record_ids_by_endpoint or {},
            )

        user_names = _load_user_names(conn)
        events = _diff_indexes(
            run_id=run_id,
            changed_at=created_at,
            previous_index=previous_index,
            current_index=current_index,
            user_names=user_names,
            delete_types_by_endpoint=deleted_record_delete_types_by_endpoint or {},
        )
        scoped_record_count = _scoped_record_count(scoped_keys)

        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                INSERT INTO endpoint_changelog_runs (
                    run_id, created_at, changelog_source, changelog_source_sha256,
                    endpoint_count, record_count, event_count, full_refresh,
                    scoped_record_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    _datetime_to_db(created_at),
                    CHANGELOG_SOURCE,
                    CHANGELOG_SOURCE_SHA,
                    len(endpoint_names),
                    len(current_index),
                    len(events),
                    int(full_refresh),
                    scoped_record_count,
                ],
            )
            event_ids = _insert_change_events_and_fields(conn, events) if events else []
            if events:
                _insert_rollups(conn, events, event_ids)
            _replace_current_index(
                conn,
                full_refresh=full_refresh,
                endpoint_names=sorted(endpoint_names),
                previous_index=previous_index,
                current_index=current_index,
                run_id=run_id,
                created_at=created_at,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return ChangelogRun(
        run_id=run_id,
        endpoint_count=len(endpoint_names),
        record_count=len(current_index),
        event_count=len(events),
        full_refresh=full_refresh,
        scoped_record_count=scoped_record_count,
    )


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


def list_change_summary(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
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


def list_actor_totals(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
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


def list_field_summary(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
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


def list_actor_summary(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
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


def list_changes(
    db_path: Path,
    *,
    endpoint: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
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
        if not table_exists(conn, "endpoint_change_events"):
            return []
        rows = conn.execute(
            f"""
            SELECT run_id, endpoint, record_id, changed_at, change_type,
                   delete_type, modified_at, modified_by_id, modified_by_name,
                   changed_fields_json, previous_payload_json, current_payload_json
            FROM endpoint_change_events
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
            "previous_payload": _json_dict(row["previous_payload_json"]),
            "current_payload": _json_dict(row["current_payload_json"]),
        }
        for row in rows
    ]


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


def _resolve_endpoint_scope(
    conn: sqlite3.Connection,
    *,
    endpoints: set[str] | None,
    record_ids_by_endpoint: dict[str, set[str]] | None,
    deleted_record_ids_by_endpoint: dict[str, set[str]] | None,
) -> set[str]:
    if endpoints is not None:
        return set(endpoints)
    scoped = set(record_ids_by_endpoint or {}) | set(deleted_record_ids_by_endpoint or {})
    if scoped:
        return scoped
    rows = conn.execute(
        """
        SELECT endpoint FROM endpoint_records
        UNION
        SELECT endpoint FROM endpoint_tombstones
        """
    ).fetchall()
    return {str(row["endpoint"]) for row in rows}


def _index_has_all_endpoints(conn: sqlite3.Connection, endpoint_names: set[str]) -> bool:
    if not endpoint_names:
        return True
    rows = conn.execute(
        f"""
        SELECT endpoint, COUNT(*) AS rows
        FROM endpoint_changelog_index_current
        WHERE endpoint IN ({",".join("?" for _ in endpoint_names)})
        GROUP BY endpoint
        """,
        sorted(endpoint_names),
    ).fetchall()
    indexed = {row["endpoint"] for row in rows if int(row["rows"] or 0) > 0}
    return endpoint_names <= indexed


def _build_current_index(
    conn: sqlite3.Connection,
    *,
    endpoints: set[str],
) -> dict[tuple[str, str], _IndexRow]:
    if not endpoints:
        return {}
    rows = conn.execute(
        f"""
        SELECT endpoint, record_id, payload_json, payload_sha256
        FROM endpoint_records
        WHERE endpoint IN ({",".join("?" for _ in endpoints)})
        ORDER BY endpoint, record_id
        """,
        sorted(endpoints),
    ).fetchall()
    return _index_from_rows(rows, hash_column="payload_sha256")


def _build_scoped_current_index(
    conn: sqlite3.Connection,
    *,
    record_ids_by_endpoint: dict[str, set[str]],
) -> dict[tuple[str, str], _IndexRow]:
    index: dict[tuple[str, str], _IndexRow] = {}
    for endpoint, record_ids in sorted(record_ids_by_endpoint.items()):
        if not record_ids:
            continue
        rows = conn.execute(
            f"""
            SELECT endpoint, record_id, payload_json, payload_sha256
            FROM endpoint_records
            WHERE endpoint = ?
              AND record_id IN ({",".join("?" for _ in record_ids)})
            ORDER BY endpoint, record_id
            """,
            [endpoint, *sorted(record_ids)],
        ).fetchall()
        index.update(_index_from_rows(rows, hash_column="payload_sha256"))
    return index


def _load_current_index(
    conn: sqlite3.Connection,
    *,
    endpoints: set[str],
) -> dict[tuple[str, str], _IndexRow]:
    if not endpoints:
        return {}
    rows = conn.execute(
        f"""
        SELECT endpoint, record_id, payload_hash, payload_json
        FROM endpoint_changelog_index_current
        WHERE endpoint IN ({",".join("?" for _ in endpoints)})
        """,
        sorted(endpoints),
    ).fetchall()
    return _index_from_rows(rows, hash_column="payload_hash")


def _load_current_index_for_keys(
    conn: sqlite3.Connection,
    *,
    keys: dict[str, set[str]],
) -> dict[tuple[str, str], _IndexRow]:
    index: dict[tuple[str, str], _IndexRow] = {}
    for endpoint, record_ids in sorted(keys.items()):
        if not record_ids:
            continue
        rows = conn.execute(
            f"""
            SELECT endpoint, record_id, payload_hash, payload_json
            FROM endpoint_changelog_index_current
            WHERE endpoint = ?
              AND record_id IN ({",".join("?" for _ in record_ids)})
            """,
            [endpoint, *sorted(record_ids)],
        ).fetchall()
        index.update(_index_from_rows(rows, hash_column="payload_hash"))
    return index


def _index_from_rows(
    rows: list[sqlite3.Row],
    *,
    hash_column: str,
) -> dict[tuple[str, str], _IndexRow]:
    return {
        (row["endpoint"], row["record_id"]): _IndexRow(
            endpoint=row["endpoint"],
            record_id=row["record_id"],
            payload_hash=row[hash_column],
            payload_json=row["payload_json"],
        )
        for row in rows
    }


def _scoped_record_keys(
    *,
    record_ids_by_endpoint: dict[str, set[str]],
    deleted_record_ids_by_endpoint: dict[str, set[str]],
) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for endpoint, record_ids in record_ids_by_endpoint.items():
        keys.setdefault(endpoint, set()).update(record_ids)
    for endpoint, record_ids in deleted_record_ids_by_endpoint.items():
        keys.setdefault(endpoint, set()).update(record_ids)
    return keys


def _diff_indexes(
    *,
    run_id: str,
    changed_at: datetime,
    previous_index: dict[tuple[str, str], _IndexRow],
    current_index: dict[tuple[str, str], _IndexRow],
    user_names: dict[str, str],
    delete_types_by_endpoint: dict[str, dict[str, str]],
) -> list[_ChangeEvent]:
    events: list[_ChangeEvent] = []
    changed_at_text = _datetime_to_db(changed_at)
    for endpoint, record_id in sorted(set(previous_index) | set(current_index)):
        previous = previous_index.get((endpoint, record_id))
        current = current_index.get((endpoint, record_id))
        if previous is None and current is not None:
            change_type = "added"
        elif previous is not None and current is None:
            change_type = "removed"
        elif previous and current and previous.payload_hash != current.payload_hash:
            change_type = "changed"
        else:
            continue

        delete_type = None
        if change_type == "removed":
            delete_type = delete_types_by_endpoint.get(endpoint, {}).get(
                record_id,
                DELETE_TYPE_UNKNOWN,
            )
        actor_payload = _actor_payload(previous=previous, current=current)
        modified_by_id = _string_value(actor_payload.get(MODIFIED_BY_FIELD))
        events.append(
            _ChangeEvent(
                run_id=run_id,
                endpoint=endpoint,
                record_id=record_id,
                changed_at=changed_at_text,
                change_type=change_type,
                delete_type=delete_type,
                modified_at=_string_value(actor_payload.get(MODIFIED_AT_FIELD)),
                modified_by_id=modified_by_id,
                modified_by_name=user_names.get(modified_by_id or ""),
                previous_hash=previous.payload_hash if previous else None,
                current_hash=current.payload_hash if current else None,
                changed_fields=_changed_fields(previous, current),
                previous_payload_json=previous.payload_json if previous else None,
                current_payload_json=current.payload_json if current else None,
            )
        )
    return events


def _insert_change_events_and_fields(
    conn: sqlite3.Connection,
    events: list[_ChangeEvent],
) -> list[int]:
    event_ids: list[int] = []
    for event in events:
        cursor = conn.execute(
            """
            INSERT INTO endpoint_change_events (
                run_id, endpoint, record_id, changed_at, change_type,
                delete_type, modified_at, modified_by_id, modified_by_name,
                previous_hash, current_hash, changed_fields_json,
                previous_payload_json, current_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event.run_id,
                event.endpoint,
                event.record_id,
                event.changed_at,
                event.change_type,
                event.delete_type,
                event.modified_at,
                event.modified_by_id,
                event.modified_by_name,
                event.previous_hash,
                event.current_hash,
                json.dumps(event.changed_fields, sort_keys=True),
                event.previous_payload_json,
                event.current_payload_json,
            ],
        )
        event_id = int(cursor.lastrowid)
        event_ids.append(event_id)
        field_rows = _field_change_rows(event_id, event)
        if field_rows:
            conn.executemany(
                """
                INSERT INTO endpoint_change_fields (
                    run_id, event_id, endpoint, record_id, changed_at, field,
                    field_change_type, event_change_type, delete_type, modified_at,
                    modified_by_id, modified_by_name, previous_value_json,
                    current_value_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                field_rows,
            )
    return event_ids


def _field_change_rows(event_id: int, event: _ChangeEvent) -> list[list[Any]]:
    previous_payload = _json_dict(event.previous_payload_json)
    current_payload = _json_dict(event.current_payload_json)
    rows: list[list[Any]] = []
    for field in sorted(set(previous_payload) | set(current_payload)):
        previous_exists = field in previous_payload
        current_exists = field in current_payload
        previous_value = previous_payload.get(field)
        current_value = current_payload.get(field)
        if previous_exists and current_exists and previous_value == current_value:
            continue
        if previous_exists and current_exists:
            field_change_type = "changed_field"
        elif current_exists:
            field_change_type = "added_field"
        else:
            field_change_type = "removed_field"
        rows.append(
            [
                event.run_id,
                event_id,
                event.endpoint,
                event.record_id,
                event.changed_at,
                field,
                field_change_type,
                event.change_type,
                event.delete_type,
                event.modified_at,
                event.modified_by_id,
                event.modified_by_name,
                _json_or_none(previous_value) if previous_exists else None,
                _json_or_none(current_value) if current_exists else None,
            ]
        )
    return rows


def _insert_rollups(
    conn: sqlite3.Connection,
    events: list[_ChangeEvent],
    event_ids: list[int],
) -> None:
    change_counts: dict[tuple[str, str, str, str | None], int] = {}
    actor_counts: dict[tuple[str, str, str | None, str | None, str, str | None], int] = {}
    field_counts: dict[tuple[str, str, str, str, str], int] = {}
    actor_field_counts: dict[tuple[str, str, str | None, str | None, str, str, str], int] = {}

    for event_id, event in zip(event_ids, events, strict=True):
        change_key = (event.run_id, event.endpoint, event.change_type, event.delete_type)
        change_counts[change_key] = change_counts.get(change_key, 0) + 1
        actor_key = (
            event.run_id,
            event.endpoint,
            event.modified_by_id,
            event.modified_by_name,
            event.change_type,
            event.delete_type,
        )
        actor_counts[actor_key] = actor_counts.get(actor_key, 0) + 1
        for field_row in _field_change_rows(event_id, event):
            field = field_row[5]
            field_change_type = field_row[6]
            event_change_type = field_row[7]
            field_key = (
                event.run_id,
                event.endpoint,
                field,
                field_change_type,
                event_change_type,
            )
            actor_field_key = (
                event.run_id,
                event.endpoint,
                event.modified_by_id,
                event.modified_by_name,
                field,
                field_change_type,
                event_change_type,
            )
            field_counts[field_key] = field_counts.get(field_key, 0) + 1
            actor_field_counts[actor_field_key] = actor_field_counts.get(actor_field_key, 0) + 1

    if change_counts:
        conn.executemany(
            """
            INSERT INTO endpoint_change_summary (
                run_id, changed_at, endpoint, change_type, delete_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                [run_id, events[0].changed_at, endpoint, change_type, delete_type, count]
                for (run_id, endpoint, change_type, delete_type), count in change_counts.items()
            ],
        )
    if field_counts:
        conn.executemany(
            """
            INSERT INTO endpoint_field_change_summary (
                run_id, changed_at, endpoint, field, field_change_type,
                event_change_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    run_id,
                    events[0].changed_at,
                    endpoint,
                    field,
                    field_change_type,
                    event_change_type,
                    count,
                ]
                for (
                    run_id,
                    endpoint,
                    field,
                    field_change_type,
                    event_change_type,
                ), count in field_counts.items()
            ],
        )
    if actor_counts:
        conn.executemany(
            """
            INSERT INTO endpoint_actor_change_summary (
                run_id, changed_at, endpoint, modified_by_id, modified_by_name,
                change_type, delete_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    run_id,
                    events[0].changed_at,
                    endpoint,
                    modified_by_id,
                    modified_by_name,
                    change_type,
                    delete_type,
                    count,
                ]
                for (
                    run_id,
                    endpoint,
                    modified_by_id,
                    modified_by_name,
                    change_type,
                    delete_type,
                ), count in actor_counts.items()
            ],
        )
    if actor_field_counts:
        conn.executemany(
            """
            INSERT INTO endpoint_actor_field_change_summary (
                run_id, changed_at, endpoint, modified_by_id, modified_by_name,
                field, field_change_type, event_change_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    run_id,
                    events[0].changed_at,
                    endpoint,
                    modified_by_id,
                    modified_by_name,
                    field,
                    field_change_type,
                    event_change_type,
                    count,
                ]
                for (
                    run_id,
                    endpoint,
                    modified_by_id,
                    modified_by_name,
                    field,
                    field_change_type,
                    event_change_type,
                ), count in actor_field_counts.items()
            ],
        )


def _replace_current_index(
    conn: sqlite3.Connection,
    *,
    full_refresh: bool,
    endpoint_names: list[str],
    previous_index: dict[tuple[str, str], _IndexRow],
    current_index: dict[tuple[str, str], _IndexRow],
    run_id: str,
    created_at: datetime,
) -> None:
    if full_refresh and endpoint_names:
        conn.execute(
            f"""
            DELETE FROM endpoint_changelog_index_current
            WHERE endpoint IN ({",".join("?" for _ in endpoint_names)})
            """,
            endpoint_names,
        )
    elif previous_index:
        conn.executemany(
            """
            DELETE FROM endpoint_changelog_index_current
            WHERE endpoint = ? AND record_id = ?
            """,
            [[endpoint, record_id] for endpoint, record_id in sorted(previous_index)],
        )
    if current_index:
        conn.executemany(
            """
            INSERT INTO endpoint_changelog_index_current (
                endpoint, record_id, payload_hash, payload_json,
                changelog_source_sha256, updated_at, run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    row.endpoint,
                    row.record_id,
                    row.payload_hash,
                    row.payload_json,
                    CHANGELOG_SOURCE_SHA,
                    _datetime_to_db(created_at),
                    run_id,
                ]
                for row in current_index.values()
            ],
        )


def _changed_fields(previous: _IndexRow | None, current: _IndexRow | None) -> list[str]:
    previous_payload = _json_dict(previous.payload_json if previous else None)
    current_payload = _json_dict(current.payload_json if current else None)
    return sorted(
        field
        for field in set(previous_payload) | set(current_payload)
        if previous_payload.get(field) != current_payload.get(field)
    )


def _actor_payload(
    *,
    previous: _IndexRow | None,
    current: _IndexRow | None,
) -> dict[str, Any]:
    if current is not None:
        payload_json = current.payload_json
    elif previous is not None:
        payload_json = previous.payload_json
    else:
        payload_json = None
    return _json_dict(payload_json)


def _load_user_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT record_id, payload_json
        FROM endpoint_records
        WHERE endpoint = ?
        """,
        [USER_ENDPOINT],
    ).fetchall()
    names: dict[str, str] = {}
    for row in rows:
        payload = _json_dict(row["payload_json"])
        name = _string_value(payload.get(USER_NAME_FIELD))
        if name:
            names[str(row["record_id"])] = name
    return names


def _allocate_run_id(conn: sqlite3.Connection, created_at: datetime) -> str:
    base = f"{created_at:%Y-%m-%dT%H%M%SZ}-changelog"
    for index in range(100):
        suffix = "" if index == 0 else f"-{index + 1}"
        run_id = f"{base}{suffix}"
        row = conn.execute(
            "SELECT COUNT(*) FROM endpoint_changelog_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if not row[0]:
            return run_id
    raise RuntimeError("Could not allocate changelog run id.")


def _scoped_record_count(record_ids_by_endpoint: dict[str, set[str]] | None) -> int:
    if not record_ids_by_endpoint:
        return 0
    return sum(len(record_ids) for record_ids in record_ids_by_endpoint.values())


def _since_filter(since: datetime | None, column: str) -> tuple[str, list[Any]]:
    if since is None:
        return "", []
    return f"WHERE {column} >= ?", [_datetime_to_db(since)]


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}


def _json_or_none(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _datetime_to_db(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
