from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from centric_api.changelog import prune_changelog
from centric_api.db_schema import ensure_changelog_tables
from centric_api.store import connect


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
        runs = conn.execute("SELECT run_id FROM endpoint_changelog_runs ORDER BY run_id").fetchall()
        event_count = conn.execute("SELECT COUNT(*) FROM endpoint_change_events").fetchone()[0]
    assert [row[0] for row in runs] == ["seed"]
    assert event_count == 0
