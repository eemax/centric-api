from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..db_schema import ensure_changelog_read_indexes, ensure_changelog_tables
from ..store import PreviousRecord, connect
from .models import (
    CHANGELOG_SOURCE,
    CHANGELOG_SOURCE_SHA,
    DELETE_TYPE_HARD_DELETE,
    DELETE_TYPE_TOMBSTONE,
    DELETE_TYPE_UNKNOWN,
    MODIFIED_AT_FIELD,
    MODIFIED_BY_FIELD,
    USER_ENDPOINT,
    USER_NAME_FIELD,
    ChangelogRun,
    ProgressCallback,
    _ChangeEvent,
    _IndexRow,
)
from .utils import _datetime_to_db, _json_dict, _string_value

# Keep room for the endpoint bind parameter on SQLite builds with the classic
# 999-variable ceiling.
SQL_IN_CHUNK_SIZE = 900


def record_changelog(
    db_path: Path,
    *,
    endpoints: set[str] | None = None,
    record_ids_by_endpoint: dict[str, set[str]] | None = None,
    deleted_record_ids_by_endpoint: dict[str, set[str]] | None = None,
    deleted_record_delete_types_by_endpoint: dict[str, dict[str, str]] | None = None,
    previous_records_by_endpoint: dict[str, dict[str, PreviousRecord]] | None = None,
    full: bool = False,
    progress: ProgressCallback | None = None,
    include_event_payloads: bool = False,
    seed_empty_full: bool = False,
) -> ChangelogRun:
    created_at = datetime.now(UTC)
    with connect(db_path) as conn:
        _emit_progress(progress, "Preparing changelog tables...")
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
        full_refresh = full or not has_record_scope
        if seed_empty_full and full_refresh and not _index_has_any_endpoints(conn, endpoint_names):
            _emit_progress(progress, "Empty changelog index; seeding baseline...")
            return _seed_changelog_index_conn(
                conn,
                created_at=created_at,
                run_id=run_id,
                endpoint_names=endpoint_names,
                progress=progress,
            )
        _emit_progress(
            progress,
            f"Mode: {'full refresh' if full_refresh else 'scoped refresh'}",
        )

        if full_refresh:
            _emit_progress(progress, "Loading existing changelog index...")
            previous_index = _load_current_index(conn, endpoints=endpoint_names)
            _emit_progress(progress, "Loading current cache...")
            current_index = _build_current_index(conn, endpoints=endpoint_names)
            tombstone_index = _build_tombstone_index(conn, endpoints=endpoint_names)
        else:
            _emit_progress(progress, "Loading scoped changelog index...")
            previous_index = _load_current_index_for_keys(conn, keys=scoped_keys)
            previous_index.update(
                _previous_records_index(previous_records_by_endpoint or {})
            )
            _emit_progress(progress, "Loading scoped cache records...")
            current_index = _build_scoped_current_index(
                conn,
                record_ids_by_endpoint=record_ids_by_endpoint or {},
            )
            tombstone_index = _build_scoped_tombstone_index(
                conn,
                deleted_record_ids_by_endpoint=deleted_record_ids_by_endpoint or {},
            )

        _emit_progress(progress, "Loading user names...")
        user_names = _load_user_names(conn)
        _emit_progress(progress, "Diffing records...")
        events = _diff_indexes(
            run_id=run_id,
            changed_at=created_at,
            previous_index=previous_index,
            current_index=current_index,
            tombstone_index=tombstone_index,
            user_names=user_names,
            delete_types_by_endpoint=deleted_record_delete_types_by_endpoint or {},
            include_event_payloads=include_event_payloads,
        )
        scoped_record_count = _scoped_record_count(scoped_keys)

        _emit_progress(progress, "Writing changelog tables...")
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
                _insert_change_events(conn, events)
                _insert_rollups(conn, events)
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
        _emit_progress(progress, "Preparing changelog read indexes...")
        ensure_changelog_read_indexes(conn)

    return ChangelogRun(
        run_id=run_id,
        endpoint_count=len(endpoint_names),
        record_count=len(current_index),
        event_count=len(events),
        full_refresh=full_refresh,
        scoped_record_count=scoped_record_count,
    )


def seed_changelog_index(
    db_path: Path,
    *,
    endpoints: set[str] | None = None,
    progress: ProgressCallback | None = None,
) -> ChangelogRun:
    """Seed the compact changelog baseline without writing synthetic events."""
    created_at = datetime.now(UTC)
    with connect(db_path) as conn:
        _emit_progress(progress, "Preparing changelog tables...")
        ensure_changelog_tables(conn)
        run_id = _allocate_run_id(conn, created_at)
        endpoint_names = _resolve_endpoint_scope(
            conn,
            endpoints=endpoints,
            record_ids_by_endpoint=None,
            deleted_record_ids_by_endpoint=None,
        )
        return _seed_changelog_index_conn(
            conn,
            created_at=created_at,
            run_id=run_id,
            endpoint_names=endpoint_names,
            progress=progress,
        )


def prune_changelog(
    db_path: Path,
    *,
    older_than: datetime,
) -> dict[str, int]:
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        cutoff = _datetime_to_db(older_than)
        event_count = _delete_count(
            conn,
            "endpoint_change_events",
            "COALESCE(modified_at, changed_at) < ?",
            [cutoff],
        )
        change_summary_count = _delete_count(
            conn,
            "endpoint_change_summary",
            "changed_at < ?",
            [cutoff],
        )
        actor_summary_count = _delete_count(
            conn,
            "endpoint_actor_change_summary",
            "changed_at < ?",
            [cutoff],
        )
        run_count = _delete_count(
            conn,
            "endpoint_changelog_runs",
            "created_at < ? AND event_count > 0",
            [cutoff],
        )
    return {
        "events": event_count,
        "change_summary": change_summary_count,
        "actor_summary": actor_summary_count,
        "runs": run_count,
    }


def _emit_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _delete_count(
    conn: sqlite3.Connection,
    table: str,
    where: str,
    params: list[Any],
) -> int:
    cursor = conn.execute(f"DELETE FROM {table} WHERE {where}", params)
    return int(cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0)


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


def _index_has_any_endpoints(conn: sqlite3.Connection, endpoint_names: set[str]) -> bool:
    if not endpoint_names:
        return False
    row = conn.execute(
        f"""
        SELECT 1
        FROM endpoint_changelog_index_current
        WHERE endpoint IN ({",".join("?" for _ in endpoint_names)})
        LIMIT 1
        """,
        sorted(endpoint_names),
    ).fetchone()
    return row is not None


def _seed_changelog_index_conn(
    conn: sqlite3.Connection,
    *,
    created_at: datetime,
    run_id: str,
    endpoint_names: set[str],
    progress: ProgressCallback | None,
) -> ChangelogRun:
    _emit_progress(progress, "Counting current cache...")
    record_count = _count_current_records(conn, endpoints=endpoint_names)
    _emit_progress(progress, "Writing changelog index...")
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
                record_count,
                0,
                1,
                0,
            ],
        )
        _replace_current_index_from_records(
            conn,
            endpoint_names=sorted(endpoint_names),
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
        record_count=record_count,
        event_count=0,
        full_refresh=True,
        scoped_record_count=0,
    )


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


def _count_current_records(
    conn: sqlite3.Connection,
    *,
    endpoints: set[str],
) -> int:
    if not endpoints:
        return 0
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM endpoint_records
        WHERE endpoint IN ({",".join("?" for _ in endpoints)})
        """,
        sorted(endpoints),
    ).fetchone()
    return int(row[0] or 0)


def _build_scoped_current_index(
    conn: sqlite3.Connection,
    *,
    record_ids_by_endpoint: dict[str, set[str]],
) -> dict[tuple[str, str], _IndexRow]:
    index: dict[tuple[str, str], _IndexRow] = {}
    for endpoint, record_ids in sorted(record_ids_by_endpoint.items()):
        for record_id_chunk in _record_id_chunks(record_ids):
            rows = conn.execute(
                f"""
                SELECT endpoint, record_id, payload_json, payload_sha256
                FROM endpoint_records
                WHERE endpoint = ?
                  AND record_id IN ({",".join("?" for _ in record_id_chunk)})
                ORDER BY endpoint, record_id
                """,
                [endpoint, *record_id_chunk],
            ).fetchall()
            index.update(_index_from_rows(rows, hash_column="payload_sha256"))
    return index


def _build_tombstone_index(
    conn: sqlite3.Connection,
    *,
    endpoints: set[str],
) -> dict[tuple[str, str], _IndexRow]:
    if not endpoints:
        return {}
    rows = conn.execute(
        f"""
        SELECT endpoint, record_id, payload_json, payload_sha256
        FROM endpoint_tombstones
        WHERE endpoint IN ({",".join("?" for _ in endpoints)})
        ORDER BY endpoint, record_id
        """,
        sorted(endpoints),
    ).fetchall()
    return _index_from_rows(rows, hash_column="payload_sha256")


def _build_scoped_tombstone_index(
    conn: sqlite3.Connection,
    *,
    deleted_record_ids_by_endpoint: dict[str, set[str]],
) -> dict[tuple[str, str], _IndexRow]:
    index: dict[tuple[str, str], _IndexRow] = {}
    for endpoint, record_ids in sorted(deleted_record_ids_by_endpoint.items()):
        for record_id_chunk in _record_id_chunks(record_ids):
            rows = conn.execute(
                f"""
                SELECT endpoint, record_id, payload_json, payload_sha256
                FROM endpoint_tombstones
                WHERE endpoint = ?
                  AND record_id IN ({",".join("?" for _ in record_id_chunk)})
                ORDER BY endpoint, record_id
                """,
                [endpoint, *record_id_chunk],
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
        SELECT endpoint, record_id, payload_hash
        FROM endpoint_changelog_index_current
        WHERE endpoint IN ({",".join("?" for _ in endpoints)})
        """,
        sorted(endpoints),
    ).fetchall()
    return _index_from_rows(rows, hash_column="payload_hash", payload_column=None)


def _load_current_index_for_keys(
    conn: sqlite3.Connection,
    *,
    keys: dict[str, set[str]],
) -> dict[tuple[str, str], _IndexRow]:
    index: dict[tuple[str, str], _IndexRow] = {}
    for endpoint, record_ids in sorted(keys.items()):
        for record_id_chunk in _record_id_chunks(record_ids):
            rows = conn.execute(
                f"""
                SELECT endpoint, record_id, payload_hash
                FROM endpoint_changelog_index_current
                WHERE endpoint = ?
                  AND record_id IN ({",".join("?" for _ in record_id_chunk)})
                """,
                [endpoint, *record_id_chunk],
            ).fetchall()
            index.update(
                _index_from_rows(rows, hash_column="payload_hash", payload_column=None)
            )
    return index


def _record_id_chunks(record_ids: set[str]) -> Iterator[list[str]]:
    sorted_ids = sorted(record_ids)
    for index in range(0, len(sorted_ids), SQL_IN_CHUNK_SIZE):
        yield sorted_ids[index : index + SQL_IN_CHUNK_SIZE]


def _index_from_rows(
    rows: list[sqlite3.Row],
    *,
    hash_column: str,
    payload_column: str | None = "payload_json",
) -> dict[tuple[str, str], _IndexRow]:
    return {
        (row["endpoint"], row["record_id"]): _IndexRow(
            endpoint=row["endpoint"],
            record_id=row["record_id"],
            payload_hash=row[hash_column],
            payload_json=row[payload_column] if payload_column else None,
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


def _previous_records_index(
    previous_records_by_endpoint: dict[str, dict[str, PreviousRecord]],
) -> dict[tuple[str, str], _IndexRow]:
    rows: dict[tuple[str, str], _IndexRow] = {}
    for endpoint, records in previous_records_by_endpoint.items():
        for record_id, previous in records.items():
            rows[(endpoint, record_id)] = _IndexRow(
                endpoint=endpoint,
                record_id=record_id,
                payload_hash=previous.payload_hash,
                payload_json=previous.payload_json,
            )
    return rows


def _diff_indexes(
    *,
    run_id: str,
    changed_at: datetime,
    previous_index: dict[tuple[str, str], _IndexRow],
    current_index: dict[tuple[str, str], _IndexRow],
    tombstone_index: dict[tuple[str, str], _IndexRow],
    user_names: dict[str, str],
    delete_types_by_endpoint: dict[str, dict[str, str]],
    include_event_payloads: bool,
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
        tombstone = (
            tombstone_index.get((endpoint, record_id)) if change_type == "removed" else None
        )
        if change_type == "removed":
            delete_type = delete_types_by_endpoint.get(endpoint, {}).get(
                record_id,
                DELETE_TYPE_TOMBSTONE if tombstone is not None else DELETE_TYPE_UNKNOWN,
            )
        actor_payload = _actor_payload(
            previous=previous,
            current=current,
            tombstone=tombstone,
            delete_type=delete_type,
        )
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
                previous_payload_json=(
                    previous.payload_json if include_event_payloads and previous else None
                ),
                current_payload_json=(
                    current.payload_json if include_event_payloads and current else None
                ),
            )
        )
    return events


def _insert_change_events(
    conn: sqlite3.Connection,
    events: list[_ChangeEvent],
) -> None:
    for event in events:
        conn.execute(
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


def _insert_rollups(
    conn: sqlite3.Connection,
    events: list[_ChangeEvent],
) -> None:
    change_counts: dict[tuple[str, str, str, str | None], int] = {}
    actor_counts: dict[tuple[str, str, str | None, str | None, str, str | None], int] = {}

    for event in events:
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
                endpoint, record_id, payload_hash,
                changelog_source_sha256, updated_at, run_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    row.endpoint,
                    row.record_id,
                    row.payload_hash,
                    CHANGELOG_SOURCE_SHA,
                    _datetime_to_db(created_at),
                    run_id,
                ]
                for row in current_index.values()
            ],
        )


def _replace_current_index_from_records(
    conn: sqlite3.Connection,
    *,
    endpoint_names: list[str],
    run_id: str,
    created_at: datetime,
) -> None:
    if not endpoint_names:
        return
    placeholders = ",".join("?" for _ in endpoint_names)
    conn.execute(
        f"""
        DELETE FROM endpoint_changelog_index_current
        WHERE endpoint IN ({placeholders})
        """,
        endpoint_names,
    )
    updated_at = _datetime_to_db(created_at)
    conn.execute(
        f"""
        INSERT INTO endpoint_changelog_index_current (
            endpoint, record_id, payload_hash,
            changelog_source_sha256, updated_at, run_id
        )
        SELECT endpoint, record_id, payload_sha256, ?, ?, ?
        FROM endpoint_records
        WHERE endpoint IN ({placeholders})
        """,
        [CHANGELOG_SOURCE_SHA, updated_at, run_id, *endpoint_names],
    )


def _changed_fields(previous: _IndexRow | None, current: _IndexRow | None) -> list[str]:
    if (
        previous is not None
        and current is not None
        and (previous.payload_json is None or current.payload_json is None)
    ):
        return []
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
    tombstone: _IndexRow | None,
    delete_type: str | None,
) -> dict[str, Any]:
    if delete_type == DELETE_TYPE_HARD_DELETE and previous is not None:
        payload_json = previous.payload_json
    elif tombstone is not None:
        payload_json = tombstone.payload_json
    elif current is not None:
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
