from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .client import FetchError

ID_FIELD = "id"
MODIFIED_AT_FIELD = "_modified_at"


@dataclass
class PageCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    restored: int = 0
    # Records not yet seen in this window that were last modified before it started, i.e.
    # records an earlier pass over the same window should have returned.
    late_finds: int = 0
    # Records modified while the window was being read (after its start, less the overlap).
    concurrent: int = 0

    def add(self, other: PageCounts) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.restored += other.restored
        self.late_finds += other.late_finds
        self.concurrent += other.concurrent


def apply_page(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    items: Iterable[dict[str, Any]],
    run_id: str,
    now: str,
    change_seq: int,
    window_started_at: str | None = None,
    changed_since: str | None = None,
) -> PageCounts:
    """Upsert one page of records. Call inside a transaction.

    A record's stored payload is only replaced by one with the same or a newer _modified_at,
    so overlapping or out-of-order pages never roll a record back. Every record seen is stamped
    with the run id, which full fetches use to detect deletions.
    """
    counts = PageCounts()
    for item in items:
        record_id = _record_id(item, endpoint)
        # Store the payload in Centric's own key order; hash a key-sorted copy so the hash only
        # changes when the content does.
        payload = json.dumps(item, separators=(",", ":"), ensure_ascii=False)
        canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        payload_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        modified_at = _text_or_none(item.get(MODIFIED_AT_FIELD))
        existing = conn.execute(
            "SELECT modified_at, payload_sha256, deleted, last_seen_run FROM bronze_records "
            "WHERE endpoint = ? AND id = ?",
            [endpoint, record_id],
        ).fetchone()
        if (
            window_started_at is not None
            and (existing is None or existing["last_seen_run"] != run_id)
            and compare_modified(modified_at, window_started_at) < 0
        ):
            counts.late_finds += 1
        if changed_since is not None and compare_modified(modified_at, changed_since) >= 0:
            counts.concurrent += 1
        if existing is None:
            conn.execute(
                """
                INSERT INTO bronze_records (
                    endpoint, id, modified_at, payload, payload_sha256, deleted, deleted_at,
                    first_seen_at, updated_at, last_seen_at, last_seen_run, change_seq
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?)
                """,
                [
                    endpoint,
                    record_id,
                    modified_at,
                    payload,
                    payload_sha256,
                    now,
                    now,
                    now,
                    run_id,
                    change_seq,
                ],
            )
            counts.inserted += 1
            continue

        replace = (
            compare_modified(modified_at, existing["modified_at"]) >= 0
            and existing["payload_sha256"] != payload_sha256
        )
        restore = bool(existing["deleted"])
        if replace:
            conn.execute(
                """
                UPDATE bronze_records
                SET modified_at = ?, payload = ?, payload_sha256 = ?, deleted = 0,
                    deleted_at = NULL, updated_at = ?, last_seen_at = ?, last_seen_run = ?,
                    change_seq = ?
                WHERE endpoint = ? AND id = ?
                """,
                [
                    modified_at,
                    payload,
                    payload_sha256,
                    now,
                    now,
                    run_id,
                    change_seq,
                    endpoint,
                    record_id,
                ],
            )
            counts.updated += 1
        elif restore:
            conn.execute(
                """
                UPDATE bronze_records
                SET deleted = 0, deleted_at = NULL, updated_at = ?, last_seen_at = ?,
                    last_seen_run = ?, change_seq = ?
                WHERE endpoint = ? AND id = ?
                """,
                [now, now, run_id, change_seq, endpoint, record_id],
            )
        else:
            conn.execute(
                "UPDATE bronze_records SET last_seen_at = ?, last_seen_run = ? "
                "WHERE endpoint = ? AND id = ?",
                [now, run_id, endpoint, record_id],
            )
            counts.unchanged += 1
        if restore:
            counts.restored += 1
    return counts


def seen_count(conn: sqlite3.Connection, *, endpoint: str, run_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM bronze_records WHERE endpoint = ? AND last_seen_run = ?",
        [endpoint, run_id],
    ).fetchone()
    return int(row["n"])


def unseen_ids(conn: sqlite3.Connection, *, endpoint: str, run_id: str) -> list[str]:
    """Active records a full fetch window did not return."""
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM bronze_records "
            "WHERE endpoint = ? AND deleted = 0 AND last_seen_run != ? ORDER BY id",
            [endpoint, run_id],
        )
    ]


def mark_deleted(
    conn: sqlite3.Connection, *, endpoint: str, ids: list[str], now: str, change_seq: int
) -> int:
    """Flag the given active records as deleted. Call inside a transaction."""
    flagged = 0
    for record_id in ids:
        cursor = conn.execute(
            """
            UPDATE bronze_records
            SET deleted = 1, deleted_at = ?, updated_at = ?, change_seq = ?
            WHERE endpoint = ? AND id = ? AND deleted = 0
            """,
            [now, now, change_seq, endpoint, record_id],
        )
        flagged += cursor.rowcount
    return flagged


def compare_modified(left: str | None, right: str | None) -> int:
    """Compare _modified_at values; a missing value sorts before any present value."""
    if left == right:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    left_dt, right_dt = _parse_datetime(left), _parse_datetime(right)
    if left_dt is not None and right_dt is not None:
        return (left_dt > right_dt) - (left_dt < right_dt)
    return (left > right) - (left < right)


def _record_id(item: Any, endpoint: str) -> str:
    if not isinstance(item, dict):
        raise FetchError(f"{endpoint}: API returned a record that is not an object.")
    value = item.get(ID_FIELD)
    if value is None or isinstance(value, dict | list | bool) or str(value).strip() == "":
        raise FetchError(f"{endpoint}: API returned a record without a usable 'id'.")
    return str(value)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
