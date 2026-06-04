from __future__ import annotations

import json

import pytest

from centric_api.changelog import record_changelog
from centric_api.cli import main
from centric_api.rendering.changelog import print_human_changelog_changes
from centric_api.store import connect
from tests.helpers_cli import _insert_endpoint_record


def test_changelog_summary_empty_db(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    exit_code = main(["changelog", "--db", str(db_path)])

    assert exit_code == 0
    assert "No changelog events found." in capsys.readouterr().out
    assert not db_path.exists()


def test_changelog_update_reports_human_progress(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"},
            payload_hash="style-1",
        )

    exit_code = main(["changelog", "update", "--db", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Updating changelog" in output
    assert f"DB:    {db_path}" in output
    assert "Scope: all endpoints" in output
    assert "Mode: full refresh" in output
    assert "Loading current cache..." in output
    assert "Diffing records..." in output
    assert "Writing changelog tables..." in output
    assert "Changelog updated:" in output


def test_changelog_update_json_suppresses_progress(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"},
            payload_hash="style-1",
        )

    exit_code = main(["changelog", "update", "--db", str(db_path), "--json"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert "Updating changelog" not in output
    assert "Loading current cache" not in output
    assert payload["endpoint_count"] == 1
    assert payload["record_count"] == 1
    assert payload["event_count"] == 1


def test_changelog_summary_human_digest_and_exit_code(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="users",
            record_id="U1",
            payload={"id": "U1", "node_name": "Ava Admin"},
            payload_hash="user-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "code": "STY-001",
                "modified_by": "U1",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            payload_hash="style-before",
        )
        _insert_endpoint_record(
            conn,
            endpoint="boms",
            record_id="B1",
            payload={
                "id": "B1",
                "code": "BOM-001",
                "modified_by": "U1",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            payload_hash="bom-before",
        )
    record_changelog(db_path, endpoints={"styles", "boms"}, full=True)
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "code": "STY-001-UPDATED",
                "modified_by": "U1",
                "_modified_at": "2026-01-02T00:00:00Z",
            },
            payload_hash="style-after",
        )
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            ["boms", "B1"],
        )
    record_changelog(
        db_path,
        record_ids_by_endpoint={"styles": {"S1"}},
        deleted_record_ids_by_endpoint={"boms": {"B1"}},
        deleted_record_delete_types_by_endpoint={"boms": {"B1": "tombstone"}},
    )

    exit_code = main(["changelog", "--db", str(db_path), "--since", "1d"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Centric API Changelog" in output
    assert "Since:    1d" in output
    assert "Totals" in output
    assert "Endpoints" in output
    assert "Modified By" in output
    assert "styles" in output
    assert "boms" in output
    assert "Ava Admin" in output
    assert "endpoint=" not in output
    assert "delete_type=" not in output


def test_changelog_summary_human_digest_honors_endpoint_filter(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"},
            payload_hash="style-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="boms",
            record_id="B1",
            payload={"id": "B1", "_modified_at": "2026-01-01T00:00:00Z"},
            payload_hash="bom-1",
        )
    record_changelog(db_path, endpoints={"styles", "boms"}, full=True)

    exit_code = main(["changelog", "--db", str(db_path), "--endpoint", "styles"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Endpoint: styles" in output
    assert "styles" in output
    assert "boms" not in output


def test_changelog_read_views_reject_repeated_endpoint_filters(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"

    exit_code = main(
        [
            "changelog",
            "fields",
            "--db",
            str(db_path),
            "--endpoint",
            "styles",
            "--endpoint",
            "boms",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "accept only one --endpoint" in captured.err


def test_changelog_runs_rejects_endpoint_filter(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"

    exit_code = main(["changelog", "runs", "--db", str(db_path), "--endpoint", "styles"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "changelog runs does not support --endpoint" in captured.err


def test_changelog_rejects_negative_limit(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"

    with pytest.raises(SystemExit) as exc_info:
        main(["changelog", "--db", str(db_path), "--limit", "-1"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "value must be a positive integer" in captured.err


def test_changelog_detail_actions_use_human_tables(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="users",
            record_id="U1",
            payload={"id": "U1", "node_name": "Ava Admin"},
            payload_hash="user-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "code": "STY-001",
                "modified_by": "U1",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            payload_hash="style-before",
        )
    record_changelog(db_path, endpoints={"styles"}, full=True)
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "code": "STY-001-UPDATED",
                "modified_by": "U1",
                "_modified_at": "2026-01-02T00:00:00Z",
            },
            payload_hash="style-after",
        )
    record_changelog(db_path, record_ids_by_endpoint={"styles": {"S1"}})

    assert main(["changelog", "fields", "--db", str(db_path)]) == 0
    fields_output = capsys.readouterr().out
    assert "Changelog Fields" in fields_output
    assert "Top changed fields" in fields_output
    assert "code" in fields_output
    assert "field_change_type=" not in fields_output

    assert main(["changelog", "actors", "--db", str(db_path)]) == 0
    actors_output = capsys.readouterr().out
    assert "Changelog Actors" in actors_output
    assert "Ava Admin" in actors_output
    assert "modified_by_id=" not in actors_output

    assert main(["changelog", "changes", "--db", str(db_path)]) == 0
    changes_output = capsys.readouterr().out
    assert "Changelog Changes" in changes_output
    assert "Modified" in changes_output
    assert "styles" in changes_output
    assert "changed_fields_json=" not in changes_output


def test_changelog_leaderboard_limits_actors_not_endpoints(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="users",
            record_id="U1",
            payload={"id": "U1", "node_name": "Ava Admin"},
            payload_hash="user-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="users",
            record_id="U2",
            payload={"id": "U2", "node_name": "Ben Buyer"},
            payload_hash="user-2",
        )
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "modified_by": "U1",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            payload_hash="style-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="boms",
            record_id="B1",
            payload={
                "id": "B1",
                "modified_by": "U1",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            payload_hash="bom-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={
                "id": "D1",
                "modified_by": "U2",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            payload_hash="document-1",
        )
    record_changelog(db_path, endpoints={"styles", "boms", "documents"}, full=True)
    with connect(db_path) as conn:
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
    )

    assert main(["changelog", "leaderboard", "--db", str(db_path), "--limit", "1"]) == 0
    output = capsys.readouterr().out
    assert "Centric API Leaderboard" in output
    assert "Records touched: 5" in output
    assert "Actors:          2" in output
    assert "Endpoint Breakdown" in output
    assert "Tomb" not in output
    assert "Hard" not in output
    assert "Unknown" not in output
    assert "Ava Admin" in output
    assert "styles" in output
    assert "boms" in output
    assert "Ben Buyer" not in output

    assert main(["changelog", "leaderboard", "--db", str(db_path), "--limit", "2"]) == 0
    output = capsys.readouterr().out
    assert "Tomb" in output
    assert "Hard" in output
    assert "Unknown" not in output
    assert "Ben Buyer" in output
    assert "1. Ava Admin" in output
    assert "2. Ben Buyer" in output

    assert main(["changelog", "leaderboard", "--db", str(db_path), "--limit", "1", "--json"]) == 0
    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(payloads) == 1
    assert payloads[0]["modified_by_name"] == "Ava Admin"
    assert payloads[0]["tombstone"] == 1
    assert payloads[0]["hard_delete"] == 0
    assert payloads[0]["unknown_delete"] == 0
    assert {endpoint["endpoint"] for endpoint in payloads[0]["endpoints"]} == {"styles", "boms"}
    boms = next(endpoint for endpoint in payloads[0]["endpoints"] if endpoint["endpoint"] == "boms")
    assert boms["removed"] == 1
    assert boms["tombstone"] == 1


def test_changelog_changes_summarizes_added_payload_fields(capsys) -> None:
    print_human_changelog_changes(
        [
            {
                "endpoint": "document_revisions",
                "record_id": "R1",
                "change_type": "added",
                "delete_type": None,
                "modified_at": "2026-01-01T00:00:00Z",
                "changed_at": "2026-01-01T00:00:00Z",
                "modified_by_name": None,
                "modified_by_id": None,
                "changed_fields": [
                    "_modified_at",
                    "modified_by",
                    "field_1",
                    "field_2",
                    "field_3",
                    "field_4",
                    "field_5",
                    "field_6",
                    "field_7",
                ],
            }
        ],
        since=None,
        endpoint=None,
    )

    output = capsys.readouterr().out
    assert "9 fields added" in output
    assert "field_1" not in output
