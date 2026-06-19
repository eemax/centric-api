from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import centric_api._changelog.recording as recording
from centric_api.changelog import list_actor_summary, list_changes, record_changelog
from centric_api.store import PreviousRecord, connect
from tests.helpers_changelog import _previous_records


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
    )
    changes = list_changes(db_path, endpoint="styles", limit=10)
    actor_summary = list_actor_summary(db_path, endpoint="styles", limit=10)
    changed_event = next(change for change in changes if change["change_type"] == "changed")

    assert second.event_count == 1
    assert changed_event["changed_fields"] == ["_modified_at", "extra"]
    assert set(changed_event) == {
        "change_type",
        "changed_at",
        "changed_fields",
        "changed_fields_json",
        "delete_type",
        "endpoint",
        "modified_at",
        "modified_by_id",
        "modified_by_name",
        "record_id",
        "run_id",
    }
    assert changed_event["modified_by_id"] == "U1"
    assert changed_event["modified_by_name"] == "Ava Admin"
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


def test_changelog_changes_return_event_metadata(tmp_path: Path) -> None:
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
        for change in list_changes(db_path, endpoint="styles", limit=10)
        if change["change_type"] == "changed"
    )
    assert changed_event["changed_fields"] == ["_modified_at", "code"]
    assert set(changed_event) == {
        "change_type",
        "changed_at",
        "changed_fields",
        "changed_fields_json",
        "delete_type",
        "endpoint",
        "modified_at",
        "modified_by_id",
        "modified_by_name",
        "record_id",
        "run_id",
    }


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
