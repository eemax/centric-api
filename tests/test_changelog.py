from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import centric_api._changelog.recording as recording
from centric_api.changelog import (
    list_actor_leaderboard,
    list_actor_summary,
    list_actor_totals,
    list_change_summary,
    list_changelog_runs,
    list_changes,
    prune_changelog,
    record_changelog,
)
from centric_api.db_schema import ensure_changelog_tables
from centric_api.store import PreviousRecord, connect


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


def _previous_records(conn, pairs: list[tuple[str, str]]) -> dict[str, dict[str, PreviousRecord]]:
    records: dict[str, dict[str, PreviousRecord]] = {}
    for endpoint, record_id in pairs:
        row = conn.execute(
            """
            SELECT payload_json, payload_sha256
            FROM endpoint_records
            WHERE endpoint = ? AND record_id = ?
            """,
            [endpoint, record_id],
        ).fetchone()
        records.setdefault(endpoint, {})[record_id] = PreviousRecord(
            payload_hash=row[1],
            payload_json=row[0],
        )
    return records


def test_changelog_tracks_full_payload_changes(tmp_path: Path) -> None:
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
                "styles",
                "S1",
                json.dumps(
                    {
                        "id": "S1",
                        "code": "A",
                        "extra": "before",
                        "modified_by": "U1",
                        "_modified_at": "2026-01-01T00:00:00Z",
                    },
                    sort_keys=True,
                ),
                "hash-before",
                "2026-01-01T00:00:00Z",
                "raw.jsonl",
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
                "U1",
                json.dumps({"id": "U1", "node_name": "Ava Admin"}, sort_keys=True),
                "hash-user",
                "2026-01-01T00:00:00Z",
                "users.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )
    first = record_changelog(db_path, endpoints={"styles"}, full=True)
    assert first.event_count == 1

    with sqlite3.connect(db_path) as conn:
        previous_row = conn.execute(
            """
            SELECT payload_json, payload_sha256
            FROM endpoint_records
            WHERE endpoint = ? AND record_id = ?
            """,
            ["styles", "S1"],
        ).fetchone()
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            [
                json.dumps(
                    {
                        "id": "S1",
                        "code": "A",
                        "extra": "after",
                        "modified_by": "U1",
                        "_modified_at": "2026-01-02T00:00:00Z",
                    },
                    sort_keys=True,
                ),
                "hash-after",
                "styles",
                "S1",
            ],
        )

    second = record_changelog(
        db_path,
        endpoints={"styles"},
        record_ids_by_endpoint={"styles": {"S1"}},
        previous_records_by_endpoint={
            "styles": {
                "S1": PreviousRecord(
                    payload_hash=previous_row[1],
                    payload_json=previous_row[0],
                )
            }
        },
        include_event_payloads=True,
    )
    changes = list_changes(db_path, endpoint="styles", limit=10)
    actor_summary = list_actor_summary(db_path, endpoint="styles", limit=10)
    changed_event = next(change for change in changes if change["change_type"] == "changed")

    assert second.event_count == 1
    assert changed_event["changed_fields"] == ["_modified_at", "extra"]
    assert changed_event["previous_payload"]["extra"] == "before"
    assert changed_event["current_payload"]["extra"] == "after"
    assert changed_event["modified_by_id"] == "U1"
    assert changed_event["modified_by_name"] == "Ava Admin"
    lightweight_changes = list_changes(
        db_path,
        endpoint="styles",
        limit=10,
        include_payloads=False,
    )
    lightweight_changed_event = next(
        change for change in lightweight_changes if change["change_type"] == "changed"
    )
    assert lightweight_changed_event["changed_fields"] == ["_modified_at", "extra"]
    assert "previous_payload" not in lightweight_changed_event
    assert "current_payload" not in lightweight_changed_event
    assert {
        (row["modified_by_id"], row["modified_by_name"], row["change_type"]): row["count"]
        for row in actor_summary
    } == {
        ("U1", "Ava Admin", "added"): 1,
        ("U1", "Ava Admin", "changed"): 1,
    }
    with sqlite3.connect(db_path) as conn:
        field_tables = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'endpoint_change_fields',
                  'endpoint_field_change_summary',
                  'endpoint_actor_field_change_summary'
              )
            ORDER BY name
            """
        ).fetchall()
    assert field_tables == []


def test_changelog_omits_event_payload_snapshots_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    before_payload = {"id": "S1", "code": "A", "_modified_at": "2026-01-01T00:00:00Z"}
    after_payload = {"id": "S1", "code": "B", "_modified_at": "2026-01-02T00:00:00Z"}
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
                "styles",
                "S1",
                json.dumps(before_payload, sort_keys=True),
                "hash-before",
                "2026-01-01T00:00:00Z",
                "styles.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )
    record_changelog(db_path, endpoints={"styles"}, full=True)
    with sqlite3.connect(db_path) as conn:
        previous_records = _previous_records(conn, [("styles", "S1")])
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?, modified_at = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            [
                json.dumps(after_payload, sort_keys=True),
                "hash-after",
                "2026-01-02T00:00:00Z",
                "styles",
                "S1",
            ],
        )

    record_changelog(
        db_path,
        record_ids_by_endpoint={"styles": {"S1"}},
        previous_records_by_endpoint=previous_records,
    )

    changed_event = next(
        change
        for change in list_changes(db_path, endpoint="styles", limit=10, include_payloads=True)
        if change["change_type"] == "changed"
    )
    assert changed_event["changed_fields"] == ["_modified_at", "code"]
    assert changed_event["previous_payload"] == {}
    assert changed_event["current_payload"] == {}


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


def test_changelog_scoped_refresh_stays_scoped_when_index_source_is_stale(tmp_path: Path) -> None:
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
                "styles",
                "S1",
                json.dumps({"id": "S1", "code": "A"}, sort_keys=True),
                "hash-before",
                "2026-01-01T00:00:00Z",
                "raw.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )
    record_changelog(db_path, endpoints={"styles"}, full=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE endpoint_changelog_index_current
            SET changelog_source_sha256 = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            ["stale-source", "styles", "S1"],
        )
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            [
                json.dumps({"id": "S1", "code": "B"}, sort_keys=True),
                "hash-after",
                "styles",
                "S1",
            ],
        )

    run = record_changelog(db_path, record_ids_by_endpoint={"styles": {"S1"}})

    assert run.full_refresh is False
    assert run.event_count == 1


def test_changelog_scoped_refresh_chunks_large_record_id_sets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(recording, "SQL_IN_CHUNK_SIZE", 2)
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        for index in range(5):
            conn.execute(
                """
                INSERT INTO endpoint_records (
                    endpoint, record_id, payload_json, payload_sha256, modified_at,
                    source_file, source_run_id, ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "styles",
                    f"S{index}",
                    json.dumps({"id": f"S{index}", "code": "A"}, sort_keys=True),
                    f"hash-before-{index}",
                    "2026-01-01T00:00:00Z",
                    "raw.jsonl",
                    "run-1",
                    "2026-01-01T00:00:00Z",
                ],
            )
    record_changelog(db_path, endpoints={"styles"}, full=True)
    with connect(db_path) as conn:
        for index in range(5):
            conn.execute(
                """
                UPDATE endpoint_records
                SET payload_json = ?, payload_sha256 = ?
                WHERE endpoint = ? AND record_id = ?
                """,
                [
                    json.dumps({"id": f"S{index}", "code": "B"}, sort_keys=True),
                    f"hash-after-{index}",
                    "styles",
                    f"S{index}",
                ],
            )

    run = record_changelog(
        db_path,
        record_ids_by_endpoint={"styles": {f"S{index}" for index in range(5)}},
    )

    assert run.full_refresh is False
    assert run.scoped_record_count == 5
    assert run.event_count == 5

    with connect(db_path) as conn:
        for index in range(5):
            conn.execute(
                "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
                ["styles", f"S{index}"],
            )
            conn.execute(
                """
                INSERT INTO endpoint_tombstones (
                    endpoint, record_id, payload_json, payload_sha256, modified_at,
                    source_file, source_run_id, ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "styles",
                    f"S{index}",
                    json.dumps(
                        {
                            "id": f"S{index}",
                            "code": "B",
                            "modified_by": "U1",
                        },
                        sort_keys=True,
                    ),
                    f"hash-tombstone-{index}",
                    "2026-01-02T00:00:00Z",
                    "styles.jsonl",
                    "run-2",
                    "2026-01-02T00:00:00Z",
                ],
            )

    delete_run = record_changelog(
        db_path,
        deleted_record_ids_by_endpoint={"styles": {f"S{index}" for index in range(5)}},
    )

    assert delete_run.full_refresh is False
    assert delete_run.scoped_record_count == 5
    assert delete_run.event_count == 5
    assert {
        row["delete_type"]
        for row in list_changes(db_path, endpoint="styles", limit=20)
        if row["change_type"] == "removed"
    } == {"tombstone"}


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


def test_changelog_records_delete_type_for_removed_records(tmp_path: Path) -> None:
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
                "styles",
                "S1",
                json.dumps(
                    {
                        "id": "S1",
                        "code": "A",
                        "modified_by": "U1",
                        "_modified_at": "2026-01-01T00:00:00Z",
                    },
                    sort_keys=True,
                ),
                "hash-before",
                "2026-01-01T00:00:00Z",
                "raw.jsonl",
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
                "U1",
                json.dumps({"id": "U1", "node_name": "Ava Admin"}, sort_keys=True),
                "hash-user",
                "2026-01-01T00:00:00Z",
                "users.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )

    record_changelog(db_path, endpoints={"styles"}, full=True)
    with sqlite3.connect(db_path) as conn:
        previous_records = _previous_records(conn, [("styles", "S1")])
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            ["styles", "S1"],
        )

    result = record_changelog(
        db_path,
        endpoints={"styles"},
        deleted_record_ids_by_endpoint={"styles": {"S1"}},
        deleted_record_delete_types_by_endpoint={"styles": {"S1": "hard_delete"}},
        previous_records_by_endpoint=previous_records,
    )

    removed_event = next(
        change
        for change in list_changes(db_path, endpoint="styles", limit=10)
        if change["change_type"] == "removed"
    )
    actor_summary = list_actor_summary(db_path, endpoint="styles", limit=10)

    assert result.event_count == 1
    assert result.scoped_record_count == 1
    assert removed_event["delete_type"] == "hard_delete"
    assert removed_event["modified_by_id"] == "U1"
    assert removed_event["modified_by_name"] == "Ava Admin"
    assert any(
        row["change_type"] == "removed"
        and row["delete_type"] == "hard_delete"
        and row["modified_by_name"] == "Ava Admin"
        for row in actor_summary
    )


def test_hard_delete_uses_previous_record_actor_when_tombstone_has_no_actor(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record_payload = json.dumps(
            {
                "id": "S1",
                "code": "A",
                "modified_by": "U1",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            sort_keys=True,
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
                "styles",
                "S1",
                _insert_record_payload,
                "hash-before",
                "2026-01-01T00:00:00Z",
                "styles.jsonl",
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
                "U1",
                json.dumps({"id": "U1", "node_name": "Ava Admin"}, sort_keys=True),
                "hash-user",
                "2026-01-01T00:00:00Z",
                "users.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )

    record_changelog(db_path, endpoints={"styles"}, full=True)
    with sqlite3.connect(db_path) as conn:
        previous_records = _previous_records(conn, [("styles", "S1")])
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            ["styles", "S1"],
        )
        conn.execute(
            """
            INSERT INTO endpoint_tombstones (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "styles",
                "S1",
                json.dumps({"id": "S1", "_delete_type": "hard_delete"}, sort_keys=True),
                "hard-delete-hash",
                None,
                "styles.jsonl",
                "run-2",
                "2026-01-02T00:00:00Z",
            ],
        )

    record_changelog(
        db_path,
        endpoints={"styles"},
        deleted_record_ids_by_endpoint={"styles": {"S1"}},
        deleted_record_delete_types_by_endpoint={"styles": {"S1": "hard_delete"}},
        previous_records_by_endpoint=previous_records,
    )

    removed_event = next(
        row
        for row in list_changes(db_path, endpoint="styles", limit=10)
        if row["change_type"] == "removed"
    )
    assert removed_event["delete_type"] == "hard_delete"
    assert removed_event["modified_by_id"] == "U1"
    assert removed_event["modified_by_name"] == "Ava Admin"


def test_changelog_allows_missing_modified_by(tmp_path: Path) -> None:
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
                "documents",
                "D1",
                json.dumps(
                    {
                        "id": "D1",
                        "node_name": "spec.pdf",
                        "_modified_at": "2026-01-01T00:00:00Z",
                    },
                    sort_keys=True,
                ),
                "hash-document",
                "2026-01-01T00:00:00Z",
                "documents.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )

    result = record_changelog(db_path, endpoints={"documents"}, full=True)
    event = list_changes(db_path, endpoint="documents", limit=10)[0]
    actor_summary = list_actor_summary(db_path, endpoint="documents", limit=10)

    assert result.event_count == 1
    assert event["modified_at"] == "2026-01-01T00:00:00Z"
    assert event["modified_by_id"] is None
    assert event["modified_by_name"] is None
    assert actor_summary == [
        {
            "endpoint": "documents",
            "modified_by_id": None,
            "modified_by_name": None,
            "change_type": "added",
            "delete_type": None,
            "count": 1,
        }
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


def test_prune_changelog_removes_old_history_but_keeps_seed_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        ensure_changelog_tables(conn)
        conn.execute(
            """
            INSERT INTO endpoint_changelog_runs (
                run_id, created_at, changelog_source, changelog_source_sha256,
                endpoint_count, record_count, event_count, full_refresh,
                scoped_record_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["seed", "2026-01-01T00:00:00Z", "test", "sha", 1, 1, 0, 1, 0],
        )
        conn.execute(
            """
            INSERT INTO endpoint_changelog_runs (
                run_id, created_at, changelog_source, changelog_source_sha256,
                endpoint_count, record_count, event_count, full_refresh,
                scoped_record_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["old-run", "2026-01-01T00:00:00Z", "test", "sha", 1, 1, 1, 0, 1],
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
                "old-run",
                "styles",
                "S1",
                "2026-01-01T00:00:00Z",
                "changed",
                None,
                "2026-01-01T00:00:00Z",
                "U1",
                "Ava Admin",
                "before",
                "after",
                json.dumps(["code"]),
                None,
                None,
            ],
        )
        conn.execute(
            """
            INSERT INTO endpoint_change_summary (
                run_id, changed_at, endpoint, change_type, delete_type, count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ["old-run", "2026-01-01T00:00:00Z", "styles", "changed", None, 1],
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
                "old-run",
                "2026-01-01T00:00:00Z",
                "styles",
                "U1",
                "Ava Admin",
                "changed",
                None,
                1,
            ],
        )

    counts = prune_changelog(
        db_path,
        older_than=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert counts["events"] == 1
    assert counts["change_summary"] == 1
    assert counts["actor_summary"] == 1
    assert counts["runs"] == 1
    with sqlite3.connect(db_path) as conn:
        runs = conn.execute(
            "SELECT run_id FROM endpoint_changelog_runs ORDER BY run_id"
        ).fetchall()
        event_count = conn.execute("SELECT COUNT(*) FROM endpoint_change_events").fetchone()[0]
    assert [row[0] for row in runs] == ["seed"]
    assert event_count == 0
