from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .store import connect

CHANGELOG_SOURCE = "full-payload"
CHANGELOG_SOURCE_SHA = hashlib.sha256(CHANGELOG_SOURCE.encode("utf-8")).hexdigest()


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


def record_changelog(
    db_path: Path,
    *,
    endpoints: set[str] | None = None,
    record_ids_by_endpoint: dict[str, set[str]] | None = None,
    deleted_record_ids_by_endpoint: dict[str, set[str]] | None = None,
    full: bool = False,
) -> ChangelogRun:
    created_at = datetime.now(UTC)
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        run_id = _allocate_run_id(conn, created_at)
        has_record_scope = (
            record_ids_by_endpoint is not None or deleted_record_ids_by_endpoint is not None
        )
        endpoint_names = _resolve_endpoint_scope(
            conn,
            endpoints=endpoints,
            record_ids_by_endpoint=record_ids_by_endpoint,
            deleted_record_ids_by_endpoint=deleted_record_ids_by_endpoint,
        )
        full_refresh = full or not has_record_scope or not _index_has_all_endpoints(
            conn,
            endpoint_names,
        )
        previous_index = _load_current_index(conn, endpoints=endpoint_names)
        if full_refresh:
            current_index = _build_current_index(conn, endpoints=endpoint_names)
        else:
            current_index = _build_scoped_current_index(
                conn,
                record_ids_by_endpoint=record_ids_by_endpoint or {},
            )
            previous_index = _filter_previous_index_for_scoped_update(
                previous_index,
                current_index=current_index,
                deleted_record_ids_by_endpoint=deleted_record_ids_by_endpoint or {},
            )

        events = _diff_indexes(
            run_id=run_id,
            changed_at=created_at,
            previous_index=previous_index,
            current_index=current_index,
        )
        scoped_record_count = _scoped_record_count(record_ids_by_endpoint)

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
            if events:
                _insert_change_events_and_fields(conn, events)
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


def ensure_changelog_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS endpoint_changelog_runs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            changelog_source TEXT NOT NULL,
            changelog_source_sha256 TEXT NOT NULL,
            endpoint_count INTEGER NOT NULL,
            record_count INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            full_refresh INTEGER NOT NULL,
            scoped_record_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endpoint_changelog_index_current (
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            changelog_source_sha256 TEXT,
            updated_at TEXT NOT NULL,
            run_id TEXT NOT NULL,
            PRIMARY KEY (endpoint, record_id)
        );

        CREATE TABLE IF NOT EXISTS endpoint_change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            change_type TEXT NOT NULL,
            previous_hash TEXT,
            current_hash TEXT,
            changed_fields_json TEXT NOT NULL,
            previous_payload_json TEXT,
            current_payload_json TEXT
        );

        CREATE TABLE IF NOT EXISTS endpoint_change_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            field TEXT NOT NULL,
            field_change_type TEXT NOT NULL,
            event_change_type TEXT NOT NULL,
            previous_value_json TEXT,
            current_value_json TEXT,
            FOREIGN KEY (event_id) REFERENCES endpoint_change_events(id)
        );

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_fields_changed_at
        ON endpoint_change_fields(changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_fields_endpoint_field
        ON endpoint_change_fields(endpoint, field);
        """
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
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
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
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    clause, params = _since_filter(since, "changed_at")
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        rows = conn.execute(
            f"""
            SELECT endpoint, change_type, COUNT(*) AS count
            FROM endpoint_change_events
            {clause}
            GROUP BY endpoint, change_type
            ORDER BY endpoint, change_type
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
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        rows = conn.execute(
            f"""
            SELECT endpoint, field, field_change_type, COUNT(*) AS count
            FROM endpoint_change_fields
            {clause}
            GROUP BY endpoint, field, field_change_type
            ORDER BY count DESC, endpoint, field, field_change_type
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
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        rows = conn.execute(
            f"""
            SELECT run_id, endpoint, record_id, changed_at, change_type,
                   changed_fields_json, previous_payload_json, current_payload_json
            FROM endpoint_change_events
            {clause}
            ORDER BY changed_at DESC, endpoint, record_id
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
    return {
        (row["endpoint"], row["record_id"]): _IndexRow(
            endpoint=row["endpoint"],
            record_id=row["record_id"],
            payload_hash=row["payload_sha256"],
            payload_json=row["payload_json"],
        )
        for row in rows
    }


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
        for row in rows:
            index[(row["endpoint"], row["record_id"])] = _IndexRow(
                endpoint=row["endpoint"],
                record_id=row["record_id"],
                payload_hash=row["payload_sha256"],
                payload_json=row["payload_json"],
            )
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
    return {
        (row["endpoint"], row["record_id"]): _IndexRow(
            endpoint=row["endpoint"],
            record_id=row["record_id"],
            payload_hash=row["payload_hash"],
            payload_json=row["payload_json"],
        )
        for row in rows
    }


def _diff_indexes(
    *,
    run_id: str,
    changed_at: datetime,
    previous_index: dict[tuple[str, str], _IndexRow],
    current_index: dict[tuple[str, str], _IndexRow],
) -> list[list[Any]]:
    events: list[list[Any]] = []
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
        events.append(
            [
                run_id,
                endpoint,
                record_id,
                _datetime_to_db(changed_at),
                change_type,
                previous.payload_hash if previous else None,
                current.payload_hash if current else None,
                json.dumps(_changed_fields(previous, current), sort_keys=True),
                previous.payload_json if previous else None,
                current.payload_json if current else None,
            ]
        )
    return events


def _insert_change_events_and_fields(
    conn: sqlite3.Connection,
    events: list[list[Any]],
) -> None:
    for event in events:
        cursor = conn.execute(
            """
            INSERT INTO endpoint_change_events (
                run_id, endpoint, record_id, changed_at, change_type,
                previous_hash, current_hash, changed_fields_json,
                previous_payload_json, current_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            event,
        )
        event_id = int(cursor.lastrowid)
        field_rows = _field_change_rows(event_id, event)
        if field_rows:
            conn.executemany(
                """
                INSERT INTO endpoint_change_fields (
                    run_id, event_id, endpoint, record_id, changed_at, field,
                    field_change_type, event_change_type, previous_value_json,
                    current_value_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                field_rows,
            )


def _field_change_rows(event_id: int, event: list[Any]) -> list[list[Any]]:
    (
        run_id,
        endpoint,
        record_id,
        changed_at,
        event_change_type,
        _previous_hash,
        _current_hash,
        _changed_fields_json,
        previous_payload_json,
        current_payload_json,
    ) = event
    previous_payload = _json_dict(previous_payload_json)
    current_payload = _json_dict(current_payload_json)
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
                run_id,
                event_id,
                endpoint,
                record_id,
                changed_at,
                field,
                field_change_type,
                event_change_type,
                _json_or_none(previous_value) if previous_exists else None,
                _json_or_none(current_value) if current_exists else None,
            ]
        )
    return rows


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


def _filter_previous_index_for_scoped_update(
    previous_index: dict[tuple[str, str], _IndexRow],
    *,
    current_index: dict[tuple[str, str], _IndexRow],
    deleted_record_ids_by_endpoint: dict[str, set[str]],
) -> dict[tuple[str, str], _IndexRow]:
    keys = set(current_index)
    for endpoint, record_ids in deleted_record_ids_by_endpoint.items():
        keys.update((endpoint, record_id) for record_id in record_ids)
    return {key: previous_index[key] for key in keys if key in previous_index}


def _changed_fields(previous: _IndexRow | None, current: _IndexRow | None) -> list[str]:
    previous_payload = _json_dict(previous.payload_json if previous else None)
    current_payload = _json_dict(current.payload_json if current else None)
    return sorted(
        field
        for field in set(previous_payload) | set(current_payload)
        if previous_payload.get(field) != current_payload.get(field)
    )


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


def _datetime_to_db(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
