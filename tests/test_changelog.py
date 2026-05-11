from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from centric_api.changelog import list_changes, list_field_summary, record_changelog
from centric_api.store import connect


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
                json.dumps({"id": "S1", "code": "A", "extra": "before"}, sort_keys=True),
                "hash-before",
                "2026-01-01T00:00:00Z",
                "raw.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )
    first = record_changelog(db_path, full=True)
    assert first.event_count == 1

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            [
                json.dumps({"id": "S1", "code": "A", "extra": "after"}, sort_keys=True),
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

    assert second.event_count == 1
    assert changes[0]["change_type"] == "changed"
    assert changes[0]["changed_fields"] == ["extra"]
    assert changes[0]["previous_payload"]["extra"] == "before"
    assert changes[0]["current_payload"]["extra"] == "after"
    assert sorted(field_summary, key=lambda row: (row["field"], row["field_change_type"])) == [
        {
            "endpoint": "styles",
            "field": "code",
            "field_change_type": "added_field",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "extra",
            "field_change_type": "added_field",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "extra",
            "field_change_type": "changed_field",
            "count": 1,
        },
        {
            "endpoint": "styles",
            "field": "id",
            "field_change_type": "added_field",
            "count": 1,
        },
    ]
