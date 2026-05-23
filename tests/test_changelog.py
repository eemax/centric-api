from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from centric_api.changelog import (
    list_actor_summary,
    list_change_summary,
    list_changelog_runs,
    list_changes,
    list_field_summary,
    record_changelog,
)
from centric_api.store import connect


def test_changelog_reads_do_not_create_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with sqlite3.connect(db_path):
        pass

    assert list_changelog_runs(db_path) == []
    assert list_change_summary(db_path) == []
    assert list_field_summary(db_path) == []
    assert list_actor_summary(db_path) == []
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
    )
    changes = list_changes(db_path, endpoint="styles", limit=10)
    field_summary = list_field_summary(db_path, endpoint="styles", limit=10)
    actor_summary = list_actor_summary(db_path, endpoint="styles", limit=10)
    field_summary_since = list_field_summary(
        db_path,
        endpoint="styles",
        since=datetime(1970, 1, 1, tzinfo=UTC),
        limit=10,
    )
    changed_event = next(change for change in changes if change["change_type"] == "changed")

    assert second.event_count == 1
    assert changed_event["changed_fields"] == ["_modified_at", "extra"]
    assert changed_event["previous_payload"]["extra"] == "before"
    assert changed_event["current_payload"]["extra"] == "after"
    assert changed_event["modified_by_id"] == "U1"
    assert changed_event["modified_by_name"] == "Ava Admin"
    assert field_summary_since
    assert {
        (row["modified_by_id"], row["modified_by_name"], row["change_type"]): row["count"]
        for row in actor_summary
    } == {
        ("U1", "Ava Admin", "added"): 1,
        ("U1", "Ava Admin", "changed"): 1,
    }
    expected_field_summary = [
        {
            "endpoint": "styles",
            "field": "_modified_at",
            "field_change_type": "added_field",
            "event_change_type": "added",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "_modified_at",
            "field_change_type": "changed_field",
            "event_change_type": "changed",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "code",
            "field_change_type": "added_field",
            "event_change_type": "added",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "extra",
            "field_change_type": "added_field",
            "event_change_type": "added",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "extra",
            "field_change_type": "changed_field",
            "event_change_type": "changed",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "id",
            "field_change_type": "added_field",
            "event_change_type": "added",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "modified_by",
            "field_change_type": "added_field",
            "event_change_type": "added",
            "count": 1,
        },
    ]
    assert sorted(field_summary, key=lambda row: (row["field"], row["field_change_type"])) == (
        expected_field_summary
    )


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
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            ["styles", "S1"],
        )

    result = record_changelog(
        db_path,
        endpoints={"styles"},
        deleted_record_ids_by_endpoint={"styles": {"S1"}},
        deleted_record_delete_types_by_endpoint={"styles": {"S1": "hard_delete"}},
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
