from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from centric_api.changelog import (
    list_actor_leaderboard,
    list_actor_summary,
    list_actor_totals,
    list_change_summary,
    list_changelog_runs,
    list_changes,
    record_changelog,
)
from centric_api.db_schema import ensure_changelog_tables
from centric_api.store import connect
from tests.helpers_changelog import _previous_records


def test_changelog_reads_do_not_create_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with sqlite3.connect(db_path):
        pass

    assert list_changelog_runs(db_path) == []
    assert list_change_summary(db_path) == []
    assert list_actor_summary(db_path) == []
    assert list_actor_leaderboard(db_path) == []
    assert list_changes(db_path) == []

    with sqlite3.connect(db_path) as conn:
        tables = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'endpoint_change%'
            """
        ).fetchall()
    assert tables == []


def test_wide_since_uses_compact_summary_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        conn.execute(
            """
            INSERT INTO endpoint_change_events (
                run_id, endpoint, record_id, changed_at, change_type, delete_type,
                modified_at, modified_by_id, modified_by_name, previous_hash,
                current_hash, changed_fields_json, previous_payload_json,
                current_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-1",
                "styles",
                "S1",
                "2026-01-02T00:00:00Z",
                "changed",
                None,
                "2026-01-02T00:00:00Z",
                "U1",
                "Ava Admin",
                "before",
                "after",
                json.dumps(["code"]),
                json.dumps({"id": "S1", "code": "A"}),
                json.dumps({"id": "S1", "code": "B"}),
            ],
        )
        conn.execute(
            """
            INSERT INTO endpoint_change_summary (
                run_id, changed_at, endpoint, change_type, delete_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ["run-1", "2026-01-02T00:00:00Z", "styles", "changed", None, 7],
        )
        conn.execute(
            """
            INSERT INTO endpoint_actor_change_summary (
                run_id, changed_at, endpoint, modified_by_id, modified_by_name,
                change_type, delete_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-1",
                "2026-01-02T00:00:00Z",
                "styles",
                "U1",
                "Ava Admin",
                "changed",
                None,
                3,
            ],
        )

    wide_since = datetime(1970, 1, 1, tzinfo=UTC)

    assert list_change_summary(db_path, since=wide_since, limit=10) == [
        {
            "endpoint": "styles",
            "change_type": "changed",
            "delete_type": None,
            "count": 7,
        }
    ]
    assert list_actor_totals(db_path, since=wide_since, limit=10) == [
        {
            "modified_by_id": "U1",
            "modified_by_name": "Ava Admin",
            "count": 3,
        }
    ]
    assert list_actor_summary(db_path, since=wide_since, limit=10) == [
        {
            "endpoint": "styles",
            "modified_by_id": "U1",
            "modified_by_name": "Ava Admin",
            "change_type": "changed",
            "delete_type": None,
            "count": 3,
        }
    ]
    assert list_actor_leaderboard(db_path, since=wide_since)[0]["total"] == 3

    with sqlite3.connect(db_path) as conn:
        activity_index = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'index'
              AND name = 'idx_endpoint_change_events_activity_at'
            """
        ).fetchone()
    assert activity_index is None


def test_changelog_actor_leaderboard_rolls_up_endpoint_footprint(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO endpoint_records (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "users",
                "U1",
                json.dumps({"id": "U1", "node_name": "Ava Admin"}, sort_keys=True),
                "hash-user-1",
                "2026-01-01T00:00:00Z",
                "users.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )
        conn.execute(
            """
            INSERT INTO endpoint_records (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "users",
                "U2",
                json.dumps({"id": "U2", "node_name": "Ben Buyer"}, sort_keys=True),
                "hash-user-2",
                "2026-01-01T00:00:00Z",
                "users.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )
        for endpoint, record_id, actor_id, payload_hash in [
            ("styles", "S1", "U1", "style-1"),
            ("styles", "S2", "U1", "style-2"),
            ("boms", "B1", "U1", "bom-1"),
            ("documents", "D1", "U2", "document-1"),
        ]:
            conn.execute(
                """
                INSERT INTO endpoint_records (
                    endpoint, record_id, payload_json, payload_sha256, modified_at,
                    source_file, source_run_id, ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    endpoint,
                    record_id,
                    json.dumps(
                        {
                            "id": record_id,
                            "name": record_id,
                            "modified_by": actor_id,
                            "_modified_at": "2026-01-01T00:00:00Z",
                        },
                        sort_keys=True,
                    ),
                    payload_hash,
                    "2026-01-01T00:00:00Z",
                    f"{endpoint}.jsonl",
                    "run-1",
                    "2026-01-01T00:00:00Z",
                ],
            )

    record_changelog(db_path, endpoints={"styles", "boms", "documents"}, full=True)
    with sqlite3.connect(db_path) as conn:
        style_previous = _previous_records(conn, [("styles", "S1")])
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?, modified_at = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            [
                json.dumps(
                    {
                        "id": "S1",
                        "name": "S1 updated",
                        "modified_by": "U1",
                        "_modified_at": "2026-01-02T00:00:00Z",
                    },
                    sort_keys=True,
                ),
                "style-1-updated",
                "2026-01-02T00:00:00Z",
                "styles",
                "S1",
            ],
        )
    record_changelog(
        db_path,
        record_ids_by_endpoint={"styles": {"S1"}},
        previous_records_by_endpoint=style_previous,
    )
    with sqlite3.connect(db_path) as conn:
        delete_previous = _previous_records(conn, [("boms", "B1"), ("documents", "D1")])
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            ["boms", "B1"],
        )
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            ["documents", "D1"],
        )
    record_changelog(
        db_path,
        deleted_record_ids_by_endpoint={"boms": {"B1"}, "documents": {"D1"}},
        deleted_record_delete_types_by_endpoint={
            "boms": {"B1": "tombstone"},
            "documents": {"D1": "hard_delete"},
        },
        previous_records_by_endpoint=delete_previous,
    )

    leaderboard = list_actor_leaderboard(db_path)

    assert [
        (
            row["modified_by_name"],
            row["total"],
            row["added"],
            row["changed"],
            row["removed"],
            row["tombstone"],
            row["hard_delete"],
            row["unknown_delete"],
        )
        for row in leaderboard
    ] == [
        ("Ava Admin", 5, 3, 1, 1, 1, 0, 0),
        ("Ben Buyer", 2, 1, 0, 1, 0, 1, 0),
    ]
    assert leaderboard[0]["endpoints"] == [
        {
            "endpoint": "styles",
            "total": 3,
            "added": 2,
            "changed": 1,
            "removed": 0,
            "tombstone": 0,
            "hard_delete": 0,
            "unknown_delete": 0,
        },
        {
            "endpoint": "boms",
            "total": 2,
            "added": 1,
            "changed": 0,
            "removed": 1,
            "tombstone": 1,
            "hard_delete": 0,
            "unknown_delete": 0,
        },
    ]


def test_list_changes_orders_by_modified_at_when_available(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO endpoint_change_events (
                run_id, endpoint, record_id, changed_at, change_type, delete_type,
                modified_at, modified_by_id, modified_by_name, previous_hash,
                current_hash, changed_fields_json, previous_payload_json,
                current_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-1",
                "styles",
                "older-detection-newer-modified",
                "2026-01-01T00:00:00Z",
                "changed",
                None,
                "2026-01-03T00:00:00Z",
                None,
                None,
                "before-1",
                "after-1",
                json.dumps(["code"]),
                json.dumps({"id": "older-detection-newer-modified"}),
                json.dumps({"id": "older-detection-newer-modified"}),
            ],
        )
        conn.execute(
            """
            INSERT INTO endpoint_change_events (
                run_id, endpoint, record_id, changed_at, change_type, delete_type,
                modified_at, modified_by_id, modified_by_name, previous_hash,
                current_hash, changed_fields_json, previous_payload_json,
                current_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-1",
                "styles",
                "newer-detection-older-modified",
                "2026-01-02T00:00:00Z",
                "changed",
                None,
                "2026-01-01T00:00:00Z",
                None,
                None,
                "before-2",
                "after-2",
                json.dumps(["code"]),
                json.dumps({"id": "newer-detection-older-modified"}),
                json.dumps({"id": "newer-detection-older-modified"}),
            ],
        )

    rows = list_changes(db_path, endpoint="styles", limit=10)

    assert [row["record_id"] for row in rows] == [
        "older-detection-newer-modified",
        "newer-detection-older-modified",
    ]

    with sqlite3.connect(db_path) as conn:
        index_names = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                  AND name LIKE 'idx_endpoint_change_events%activity_sort'
                """
            ).fetchall()
        }
    assert index_names == {
        "idx_endpoint_change_events_activity_sort",
        "idx_endpoint_change_events_endpoint_activity_sort",
    }
