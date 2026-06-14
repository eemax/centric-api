from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from .schema import DeleteCondition, EndpointSchema
from .store_discovery import RawFile

PRIMARY_KEY_FIELD = "id"
MODIFIED_AT_FIELD = "_modified_at"
HARD_DELETE_TYPE_FIELD = "_centric_api_delete_type"
HARD_DELETE_DELETED_AT_FIELD = "_centric_api_deleted_at"
HARD_DELETE_SOURCE_RUN_ID_FIELD = "_centric_api_source_run_id"
HARD_DELETE_SOURCE_FILE_FIELD = "_centric_api_source_file"
DELETE_TYPE_TOMBSTONE = "tombstone"
DELETE_TYPE_HARD_DELETE = "hard_delete"


@dataclass(frozen=True)
class ApplyRawFileResult:
    records_read: int
    records_upserted: int
    records_deleted: int
    records_hard_deleted: int
    invalid_records: int
    upserted_ids: tuple[str, ...]
    deleted_ids: tuple[str, ...]
    deleted_types: Mapping[str, str]


def _apply_raw_file(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    schema: EndpointSchema,
) -> ApplyRawFileResult:
    winners: dict[str, tuple[dict[str, Any], int]] = {}
    records_read = 0
    invalid_records = 0
    now = _utc_iso()

    with raw_file.path.open("r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            text = raw_line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                invalid_records += 1
                _insert_warning(conn, raw_file, None, f"line {line_number}: invalid JSON ({exc})")
                continue
            if not isinstance(payload, dict):
                invalid_records += 1
                _insert_warning(conn, raw_file, None, f"line {line_number}: record is not object")
                continue
            record_id = payload.get(PRIMARY_KEY_FIELD)
            if record_id is None or str(record_id).strip() == "":
                invalid_records += 1
                _insert_warning(conn, raw_file, None, f"line {line_number}: missing id")
                continue
            records_read += 1
            record_key = str(record_id)
            existing = winners.get(record_key)
            if existing is None or _record_is_newer(payload, line_number, existing[0], existing[1]):
                winners[record_key] = (payload, line_number)

    upserted_ids: set[str] = set()
    deleted_ids: set[str] = set()
    deleted_types: dict[str, str] = {}
    retained_ids: set[str] = set()
    for record_id, (payload, _) in sorted(winners.items()):
        payload_json = _canonical_json(payload)
        payload_hash = _hash_text(payload_json)
        modified_at = _string_value(payload.get(MODIFIED_AT_FIELD))
        is_delete = _matches_delete(payload, schema.delete_when_any)
        existing = conn.execute(
            """
            SELECT payload_json, payload_sha256, modified_at
            FROM endpoint_records
            WHERE endpoint = ? AND record_id = ?
            """,
            [raw_file.endpoint, record_id],
        ).fetchone()

        if is_delete:
            if existing is not None and _incoming_can_replace(modified_at, existing["modified_at"]):
                conn.execute(
                    "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
                    [raw_file.endpoint, record_id],
                )
                _upsert_tombstone(
                    conn,
                    raw_file=raw_file,
                    record_id=record_id,
                    payload_json=payload_json,
                    payload_hash=payload_hash,
                    modified_at=modified_at,
                    ingested_at=now,
                )
                deleted_ids.add(record_id)
                deleted_types[record_id] = DELETE_TYPE_TOMBSTONE
            elif existing is None:
                _upsert_tombstone(
                    conn,
                    raw_file=raw_file,
                    record_id=record_id,
                    payload_json=payload_json,
                    payload_hash=payload_hash,
                    modified_at=modified_at,
                    ingested_at=now,
                )
            continue

        retained_ids.add(record_id)

        if existing is None:
            _upsert_current_record(
                conn,
                raw_file=raw_file,
                record_id=record_id,
                payload_json=payload_json,
                payload_hash=payload_hash,
                modified_at=modified_at,
                ingested_at=now,
            )
            upserted_ids.add(record_id)
            continue

        if not _incoming_can_replace(modified_at, existing["modified_at"]):
            continue
        if existing["payload_sha256"] == payload_hash:
            continue
        if modified_at is not None and modified_at == existing["modified_at"]:
            _insert_warning(
                conn,
                raw_file,
                record_id,
                "same _modified_at with different payload; replaced by later raw file order",
            )
        _upsert_current_record(
            conn,
            raw_file=raw_file,
            record_id=record_id,
            payload_json=payload_json,
            payload_hash=payload_hash,
            modified_at=modified_at,
            ingested_at=now,
        )
        upserted_ids.add(record_id)

    hard_deleted_ids = _reconcile_full_snapshot_hard_deletes(
        conn,
        raw_file=raw_file,
        retained_ids=retained_ids,
        invalid_records=invalid_records,
        ingested_at=now,
    )
    deleted_ids.update(hard_deleted_ids)
    for record_id in hard_deleted_ids:
        deleted_types[record_id] = DELETE_TYPE_HARD_DELETE

    return ApplyRawFileResult(
        records_read=records_read,
        records_upserted=len(upserted_ids),
        records_deleted=len(deleted_ids) - len(hard_deleted_ids),
        records_hard_deleted=len(hard_deleted_ids),
        invalid_records=invalid_records,
        upserted_ids=tuple(sorted(upserted_ids)),
        deleted_ids=tuple(sorted(deleted_ids)),
        deleted_types=MappingProxyType(dict(sorted(deleted_types.items()))),
    )


def _reconcile_full_snapshot_hard_deletes(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    retained_ids: set[str],
    invalid_records: int,
    ingested_at: str,
) -> set[str]:
    if raw_file.run_mode != "full" or raw_file.is_delta or invalid_records:
        return set()

    rows = conn.execute(
        """
        SELECT record_id
        FROM endpoint_records
        WHERE endpoint = ?
        """,
        [raw_file.endpoint],
    ).fetchall()
    current_ids = {str(row["record_id"]) for row in rows}
    hard_deleted_ids = current_ids - retained_ids
    for record_id in sorted(hard_deleted_ids):
        payload = {
            PRIMARY_KEY_FIELD: record_id,
            HARD_DELETE_TYPE_FIELD: "hard_delete",
            HARD_DELETE_DELETED_AT_FIELD: ingested_at,
            HARD_DELETE_SOURCE_RUN_ID_FIELD: raw_file.source_run_id,
            HARD_DELETE_SOURCE_FILE_FIELD: str(raw_file.path),
        }
        payload_json = _canonical_json(payload)
        payload_hash = _hash_text(payload_json)
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            [raw_file.endpoint, record_id],
        )
        _upsert_tombstone(
            conn,
            raw_file=raw_file,
            record_id=record_id,
            payload_json=payload_json,
            payload_hash=payload_hash,
            modified_at=None,
            ingested_at=ingested_at,
        )
    return hard_deleted_ids


def _record_is_newer(
    candidate: dict[str, Any],
    candidate_line: int,
    existing: dict[str, Any],
    existing_line: int,
) -> bool:
    candidate_modified = _string_value(candidate.get(MODIFIED_AT_FIELD))
    existing_modified = _string_value(existing.get(MODIFIED_AT_FIELD))
    cmp = _compare_modified(candidate_modified, existing_modified)
    if cmp != 0:
        return cmp > 0
    return candidate_line > existing_line


def _incoming_can_replace(incoming_modified: str | None, existing_modified: str | None) -> bool:
    if incoming_modified is None:
        return existing_modified is None
    if existing_modified is None:
        return True
    return _compare_modified(incoming_modified, existing_modified) >= 0


def _compare_modified(left: str | None, right: str | None) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is not None and right_dt is not None:
        return (left_dt > right_dt) - (left_dt < right_dt)
    return (left > right) - (left < right)


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _matches_delete(payload: dict[str, Any], conditions: tuple[DeleteCondition, ...]) -> bool:
    return any(
        _extract_path(payload, condition.field) == condition.equals for condition in conditions
    )


def _extract_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _upsert_current_record(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    record_id: str,
    payload_json: str,
    payload_hash: str,
    modified_at: str | None,
    ingested_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint, record_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            payload_sha256 = excluded.payload_sha256,
            modified_at = excluded.modified_at,
            source_file = excluded.source_file,
            source_run_id = excluded.source_run_id,
            ingested_at = excluded.ingested_at
        """,
        [
            raw_file.endpoint,
            record_id,
            payload_json,
            payload_hash,
            modified_at,
            str(raw_file.path),
            raw_file.source_run_id,
            ingested_at,
        ],
    )
    conn.execute(
        "DELETE FROM endpoint_tombstones WHERE endpoint = ? AND record_id = ?",
        [raw_file.endpoint, record_id],
    )


def _upsert_tombstone(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    record_id: str,
    payload_json: str,
    payload_hash: str,
    modified_at: str | None,
    ingested_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO endpoint_tombstones (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint, record_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            payload_sha256 = excluded.payload_sha256,
            modified_at = excluded.modified_at,
            source_file = excluded.source_file,
            source_run_id = excluded.source_run_id,
            ingested_at = excluded.ingested_at
        """,
        [
            raw_file.endpoint,
            record_id,
            payload_json,
            payload_hash,
            modified_at,
            str(raw_file.path),
            raw_file.source_run_id,
            ingested_at,
        ],
    )


def _insert_warning(
    conn: sqlite3.Connection,
    raw_file: RawFile,
    record_id: str | None,
    warning: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_warnings (endpoint, record_id, source_file, warning, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [raw_file.endpoint, record_id, str(raw_file.path), warning, _utc_iso()],
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
