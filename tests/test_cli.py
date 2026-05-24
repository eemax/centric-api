from __future__ import annotations

import json
import sqlite3

import pytest

from centric_api.changelog import ChangelogRun, record_changelog
from centric_api.cli import main
from centric_api.cli_parser import build_parser
from centric_api.commands.common import (
    append_cron_log_event,
    append_cron_log_fetch_records,
    release_fetch_lock,
    try_acquire_fetch_lock,
)
from centric_api.commands.cron import run_cron_fetch_once
from centric_api.models import AuthSettings, CountSpec, EndpointSpec, FetcherConfig, FetchRunResult
from centric_api.rendering.changelog import print_human_changelog_changes
from centric_api.rendering.logs import render_log_line
from centric_api.runtime_io import parse_jsonl
from centric_api.store import IngestResult, connect


def test_cli_help_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "fetch" in output
    assert "changelog" in output
    assert "cron" in output
    assert "download" in output
    assert "bundle" in output
    assert "view" in output
    assert "units" in output


def test_units_cli_convert_and_normalize(capsys) -> None:
    assert main(["units", "convert", "1500", "g", "kg"]) == 0
    output = capsys.readouterr().out
    assert "1500 g = 1.5 kg (mass)" in output

    assert main(["units", "normalize", "sq m", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"input": "sq m", "unit": "m2", "dimension": "area"}


def test_units_cli_uses_explicit_config(tmp_path, capsys) -> None:
    config = tmp_path / "units.yml"
    config.write_text(
        """
version: 1
dimensions:
  volume:
    base: l
    units:
      ml:
        factor: 0.001
        aliases: [milliliter]
      l:
        factor: 1
        aliases: [liter]
""",
        encoding="utf-8",
    )

    assert main(["units", "--units-config", str(config), "convert", "500", "ml", "l"]) == 0

    output = capsys.readouterr().out
    assert "500 ml = 0.5 l (volume)" in output


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


def test_fetch_and_cron_help_are_lean(capsys) -> None:
    with pytest.raises(SystemExit) as fetch_exc:
        main(["fetch", "--help"])
    assert fetch_exc.value.code == 0
    fetch_help = capsys.readouterr().out
    assert "--fetch-config" in fetch_help

    with pytest.raises(SystemExit) as cron_exc:
        main(["cron", "--help"])
    assert cron_exc.value.code == 0
    cron_help = capsys.readouterr().out
    assert "--fetch-config" in cron_help
    assert "--log-level" not in cron_help
    assert "[schedule]" in cron_help

    with pytest.raises(SystemExit) as download_exc:
        main(["download", "--help"])
    assert download_exc.value.code == 0
    download_help = capsys.readouterr().out
    assert "--download-config" in download_help
    assert "--job" in download_help
    assert "--sync" in download_help
    assert "--rebuild" in download_help

    with pytest.raises(SystemExit) as bundle_exc:
        main(["bundle", "--help"])
    assert bundle_exc.value.code == 0
    bundle_help = capsys.readouterr().out
    assert "run" in bundle_help
    assert "list" in bundle_help
    assert "show" in bundle_help
    assert "changelog" in bundle_help

    with pytest.raises(SystemExit) as bundle_run_exc:
        main(["bundle", "run", "--help"])
    assert bundle_run_exc.value.code == 0
    bundle_run_help = capsys.readouterr().out
    assert "--bundle-config" in bundle_run_help
    assert "--job" in bundle_run_help
    assert "--no-zip" in bundle_run_help


def test_fetch_log_level_defaults_to_summary() -> None:
    args = build_parser().parse_args(["fetch"])

    assert args.log_level == "summary"


def test_fetch_log_renderer_uses_human_run_and_endpoint_lines() -> None:
    run_line = render_log_line(
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "level": "summary",
            "event": "run_start",
            "run_id": "run-1",
            "mode": "delta",
            "endpoint_count": 2,
            "endpoints": ["styles", "boms"],
            "output_dir": "/tmp/raw",
        }
    )
    endpoint_line = render_log_line(
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "level": "summary",
            "event": "endpoint_ok",
            "endpoint": "styles",
            "expected": 10,
            "fetched": 10,
            "pages": 1,
            "retries": 0,
            "duration_seconds": 1.2,
            "output": None,
        }
    )

    assert run_line == (
        "2026-01-01T00:00:00Z RUN start run_id=run-1 mode=delta "
        "endpoint_count=2 endpoints=styles,boms output_dir=/tmp/raw"
    )
    assert endpoint_line == (
        "2026-01-01T00:00:01Z ENDPOINT ok endpoint=styles expected=10 fetched=10 "
        "pages=1 retries=0 duration=1.2s"
    )


def test_fetch_exits_when_lock_exists(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "fetch.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["fetch"])

    assert exit_code == 1
    assert "fetch lock exists" in capsys.readouterr().err


def test_fetch_delta_dry_run_skips_lock_and_log(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "fetch.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["fetch", "--delta-dry-run", "--endpoint", "styles"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"status": "delta_dry_run"' in output
    assert lock_path.exists()
    assert not (tmp_path / "logs" / "fetch.log").exists()


def test_fetch_reports_post_fetch_pipeline_progress(tmp_path, monkeypatch, capsys) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fetch Complete" in captured.out
    assert "Pipeline..." in captured.err
    assert "Writing run manifest..." in captured.err
    assert "Ingesting fetched records..." in captured.err
    assert "Updating changelog..." in captured.err
    assert "  Mode: scoped refresh" in captured.err
    assert "  Writing changelog tables..." in captured.err


def test_fetch_json_suppresses_post_fetch_pipeline_progress(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db"), "--json"])

    captured = capsys.readouterr()
    records = parse_jsonl(captured.out)
    assert exit_code == 0
    assert any(record.get("record_type") == "pipeline_summary" for record in records)
    assert "Pipeline..." not in captured.err
    assert "Updating changelog..." not in captured.err


def test_download_exits_when_lock_exists(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "download.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["download"])

    assert exit_code == 1
    assert "download lock exists" in capsys.readouterr().err


def test_download_dry_run_skips_lock(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "download.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["download", "--dry-run", "--db", str(tmp_path / "missing.db")])

    assert exit_code == 1
    assert "SQLite database not found" in capsys.readouterr().err
    assert not (tmp_path / "logs" / "download.log").exists()


def test_bundle_exits_when_lock_exists(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "bundle.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["bundle"])

    assert exit_code == 1
    assert "bundle lock exists" in capsys.readouterr().err


def test_bundle_dry_run_skips_lock(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "bundle.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["bundle", "--dry-run", "--db", str(tmp_path / "missing.db")])

    assert exit_code == 1
    assert "SQLite database not found" in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()


def test_bundle_history_commands_use_bundle_run_id(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_bundle_run(conn, "2026-01-01T000000Z-style-bundle", "2026-01-01T00:00:00Z")
        _insert_bundle_run(conn, "2026-01-02T000000Z-style-bundle", "2026-01-02T00:00:00Z")
        _insert_bundle_item(
            conn,
            "2026-01-01T000000Z-style-bundle",
            "styles\x1fS1\x1fD1",
            "files/styles/Old/spec.pdf",
            "R1",
            "sha1",
        )
        _insert_bundle_item(
            conn,
            "2026-01-02T000000Z-style-bundle",
            "styles\x1fS1\x1fD1",
            "files/styles/New/spec.pdf",
            "R2",
            "sha2",
        )

    assert main(["bundle", "list", "--db", str(db_path), "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["run_id"] == "2026-01-02T000000Z-style-bundle"

    assert (
        main(
            [
                "bundle",
                "show",
                "2026-01-01T000000Z-style-bundle",
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["bundle_name"] == "style-bundle"

    assert (
        main(
            [
                "bundle",
                "changelog",
                "2026-01-01T000000Z-style-bundle",
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    changelog = json.loads(capsys.readouterr().out)
    assert changelog["summary"]["changed_count"] == 1
    assert changelog["to_run"]["run_id"] == "2026-01-02T000000Z-style-bundle"


def test_bundle_list_and_show_use_human_tables(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_bundle_run(conn, "2026-01-01T000000Z-style-bundle", "2026-01-01T00:00:00Z")
        _insert_bundle_item(
            conn,
            "2026-01-01T000000Z-style-bundle",
            "styles\x1fS1\x1fD1",
            "files/styles/Style One/spec.pdf",
            "R1",
            "sha1",
        )

    assert main(["bundle", "list", "--db", str(db_path)]) == 0
    list_output = capsys.readouterr().out
    assert "Bundle Runs" in list_output
    assert "Run" in list_output
    assert "Delta" in list_output
    assert "run_id=" not in list_output

    assert main(["bundle", "show", "2026-01-01T000000Z-style-bundle", "--db", str(db_path)]) == 0
    show_output = capsys.readouterr().out
    assert "Bundle Run" in show_output
    assert "Files" in show_output
    assert "Change" in show_output
    assert "files/styles/Style One/spec.pdf" in show_output
    assert "- added:" not in show_output


def test_fetch_lock_helpers_create_and_release_lock(tmp_path) -> None:
    lock_path = tmp_path / "fetch.lock"

    assert try_acquire_fetch_lock(lock_path) is None
    assert lock_path.is_file()
    assert try_acquire_fetch_lock(lock_path) is not None

    release_fetch_lock(lock_path)

    assert not lock_path.exists()


def test_parse_jsonl_preserves_non_json_lines() -> None:
    assert parse_jsonl('{"status":"ok"}\nnot-json\n') == [
        {"status": "ok"},
        {"record_type": "fetch_stdout", "line": "not-json"},
    ]


def test_cron_log_helpers_write_jsonl_only(tmp_path) -> None:
    log_path = tmp_path / "cron.jsonl"

    append_cron_log_event(log_path, record_type="cron_start", schedule="0 * * * *")
    append_cron_log_fetch_records(
        log_path,
        records=[{"endpoint": "styles", "status": "ok", "items_fetched": 2}],
        stderr="",
        exit_code=0,
        duration_seconds=1.2345,
    )

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    assert [row.get("record_type") for row in rows] == [
        "cron_start",
        None,
        "cron_fetch_summary",
    ]
    assert rows[0]["schedule"] == "0 * * * *"
    assert rows[1]["endpoint"] == "styles"
    assert rows[2]["exit_code"] == 0


def test_cron_fetch_logs_uncaught_fetch_errors(tmp_path, monkeypatch) -> None:
    def fail_fetch(_args):
        raise RuntimeError("boom")

    monkeypatch.setattr("centric_api.commands.cron.run_fetch", fail_fetch)
    args = build_parser().parse_args(["cron"])
    lock_path = tmp_path / "fetch.lock"
    log_path = tmp_path / "cron.jsonl"

    run_cron_fetch_once(args, lock_file=lock_path, log_file=log_path)

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["record_type"] == "fetch_stderr"
    assert "boom" in rows[0]["stderr"]
    assert rows[1]["record_type"] == "cron_fetch_summary"
    assert rows[1]["exit_code"] == 1
    assert not lock_path.exists()


def test_cron_fetch_skips_when_fetch_lock_exists(tmp_path) -> None:
    args = build_parser().parse_args(["cron"])
    lock_path = tmp_path / "fetch.lock"
    log_path = tmp_path / "cron.jsonl"
    lock_path.write_text("locked", encoding="utf-8")

    run_cron_fetch_once(args, lock_file=lock_path, log_file=log_path)

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "timestamp": rows[0]["timestamp"],
            "record_type": "cron_fetch_skipped",
            "reason": "lock_exists",
            "lock_file": str(lock_path),
            "message": f"fetch lock exists: {lock_path}",
        }
    ]
    assert lock_path.exists()


def test_status_reports_missing_db(tmp_path, capsys) -> None:
    exit_code = main(["status", "--db", str(tmp_path / "missing.db"), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["db_exists"] is False


def test_status_human_output_is_operational_snapshot(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="boms",
            record_id="B1",
            payload={"id": "B1", "_modified_at": "2026-01-02T00:00:00Z"},
            payload_hash="bom-1",
        )
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"},
            payload_hash="style-1",
        )
        _insert_applied_raw_file(conn, endpoint="boms", record_count=1)
        _insert_applied_raw_file(conn, endpoint="styles", record_count=1)
        conn.execute(
            """
            INSERT INTO endpoint_changelog_runs (
                run_id, created_at, changelog_source, changelog_source_sha256,
                endpoint_count, record_count, event_count, full_refresh,
                scoped_record_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["change-1", "2026-01-02T00:00:00Z", "test", "sha", 2, 2, 3, 1, 0],
        )
        _insert_download_run(conn)
        _insert_bundle_run(conn, "bundle-1", "2026-01-02T00:00:00Z")

    exit_code = main(["status", "--db", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Centric API Status" in output
    assert "Health" in output
    assert "Fetch lock:     clear" in output
    assert "Latest Runs" in output
    assert "Fetch:" in output
    assert "full  2 endpoints  2 records" in output
    assert "Changelog:" in output
    assert "3 events  2 endpoints" in output
    assert "Download:" in output
    assert "docs  4 downloaded, 0 failed" in output
    assert "Bundle:" in output
    assert "style-bundle  1 files" in output
    assert "Data" in output
    assert "Records:          2 current" in output
    assert "Latest modified:" in output
    assert "ago" in output
    assert "Endpoints" in output
    assert "boms" in output
    assert "styles" in output
    assert "- styles:" not in output


def test_doctor_reports_missing_db(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "https://centric.example.com")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")

    exit_code = main(["doctor", "--db", str(tmp_path / "missing.db"), "--json"])

    assert exit_code == 1
    checks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {
        "status": "FAIL",
        "name": "db",
        "message": f"SQLite database not found: {tmp_path / 'missing.db'}",
    } in checks


def test_doctor_uses_fetch_evidence_for_empty_download_endpoints(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "https://centric.example.com")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_applied_raw_file(conn, endpoint="documents")
        _insert_applied_raw_file(conn, endpoint="document_revisions")
    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
jobs:
  - name: docs
    sources:
      - endpoint: documents
""",
        encoding="utf-8",
    )

    exit_code = main(
        ["doctor", "--db", str(db_path), "--download-config", str(config_path), "--json"]
    )

    checks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert {
        "status": "OK",
        "name": "download_job:docs",
        "message": "required endpoints cached",
    } in checks


def test_doctor_reports_stale_schema_shape(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "https://centric.example.com")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")
    db_path = tmp_path / "centric.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE local_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO local_metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ["db_schema_version", "1", "2026-01-01T00:00:00Z"],
        )
        conn.execute("CREATE TABLE endpoint_records (endpoint TEXT, record_id TEXT)")

    exit_code = main(["doctor", "--db", str(db_path), "--json"])

    checks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    schema_check = next(check for check in checks if check["name"] == "db_schema_shape")
    assert exit_code == 1
    assert schema_check["status"] == "FAIL"
    assert "run centric-api rebuild-db --yes" in schema_check["message"]
    assert schema_check["repair"] == "centric-api rebuild-db --yes"


def test_doctor_human_output_is_grouped_with_repair(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "https://centric.example.com")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")
    db_path = tmp_path / "centric.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE local_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO local_metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ["db_schema_version", "1", "2026-01-01T00:00:00Z"],
        )
        conn.execute("CREATE TABLE endpoint_records (endpoint TEXT, record_id TEXT)")

    exit_code = main(["doctor", "--db", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Centric API Doctor" in output
    assert "Result: FAIL" in output
    assert "Setup" in output
    assert "Database" in output
    assert "Runtime" in output
    assert "schema shape" in output
    assert "repair: centric-api rebuild-db --yes" in output
    assert "db_schema_shape" not in output


def test_rebuild_db_requires_yes(tmp_path, capsys) -> None:
    exit_code = main(["rebuild-db", "--db", str(tmp_path / "centric.db")])

    assert exit_code == 1
    assert "rerun with --yes" in capsys.readouterr().err


def test_rebuild_db_replays_raw_evidence(tmp_path, capsys) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    exit_code = main(
        ["rebuild-db", "--db", str(db_path), "--raw-dir", str(raw_dir), "--yes", "--json"]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert "Rebuilding SQLite" not in output
    assert "Ingesting raw records" not in output
    assert payload["ingest"]["records_read"] == 1
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM endpoint_records").fetchone()[0]
    assert count == 1


def test_rebuild_db_reports_human_progress(tmp_path, capsys) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    exit_code = main(["rebuild-db", "--db", str(db_path), "--raw-dir", str(raw_dir), "--yes"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Rebuilding SQLite..." in output
    assert f"DB:  {db_path}" in output
    assert f"Raw: {raw_dir}" in output
    assert "Loading endpoint schemas..." in output
    assert "Backing up existing DB files..." in output
    assert "Ingesting raw records..." in output
    assert "Updating changelog..." in output
    assert "  Loading current cache..." in output
    assert "Opening rebuilt DB..." in output
    assert "SQLite Rebuilt" in output


def _insert_bundle_run(conn, run_id: str, finished_at: str) -> None:
    conn.execute(
        """
        INSERT INTO bundle_runs (
            run_id, bundle_name, download_job, started_at, finished_at,
            manifest_path, changelog_json_path, changelog_md_path, zip_path,
            item_count, added_count, changed_count, renamed_count, removed_count,
            unchanged_count, missing_count, dry_run
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            "style-bundle",
            "style-docs",
            finished_at,
            finished_at,
            "manifest.json",
            "changelog.json",
            "changelog.md",
            f"{run_id}.zip",
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
    )


def _insert_download_run(conn) -> None:
    conn.execute(
        """
        INSERT INTO download_runs (
            run_id, job_name, mode, started_at, finished_at, manifest_path,
            matched_count, selected_count, downloaded_count, already_present_count,
            failed_count, skipped_count, skipped_current_count, dry_run_count,
            superseded_count, tombstoned_count, dry_run
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "download-1",
            "docs",
            "delta",
            "2026-01-02T00:00:00Z",
            "2026-01-02T00:00:00Z",
            "manifest.json",
            4,
            4,
            4,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
    )


def _insert_endpoint_record(
    conn,
    *,
    endpoint: str,
    record_id: str,
    payload: dict[str, object],
    payload_hash: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            record_id,
            json.dumps(payload, sort_keys=True),
            payload_hash,
            payload.get("_modified_at"),
            f"{endpoint}.jsonl",
            "run-1",
            "2026-01-01T00:00:00Z",
        ],
    )


def _patch_fetch_pipeline(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    endpoint = EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        count_spec=CountSpec(path="count/Style"),
    )
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.load_fetcher_settings",
        lambda _path: (fetcher_cfg, AuthSettings(timeout=1), [endpoint]),
    )

    class Auth:
        base_url = "https://centric.example.com"
        timeout = 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "centric_api.commands.fetch.init_auth_context",
        lambda *_args, **_kwargs: Auth(),
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.run_endpoint",
        lambda *_args, **_kwargs: FetchRunResult(
            endpoint="styles",
            pages_fetched=1,
            items_fetched=1,
            expected_count=1,
            retries_used=0,
            start_skip=0,
            next_skip=50,
            duration_seconds=0.1,
            output_file=tmp_path / "raw" / "styles.jsonl",
            checkpoint_file=tmp_path / "checkpoints" / "styles.json",
            id_validation_checked_items=1,
            id_validation_unique_ids=1,
        ),
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.ingest_raw_dir",
        lambda *_args, **_kwargs: IngestResult(
            applied_files=1,
            skipped_files=0,
            records_read=1,
            records_upserted=1,
            records_deleted=0,
            records_hard_deleted=0,
            invalid_records=0,
            endpoints={"styles": 1},
            upserted_record_ids_by_endpoint={"styles": ("S1",)},
            deleted_record_ids_by_endpoint={},
            deleted_record_delete_types_by_endpoint={},
        ),
    )

    def fake_record_changelog(*_args, progress=None, **_kwargs):
        if progress is not None:
            progress("Mode: scoped refresh")
            progress("Writing changelog tables...")
        return ChangelogRun(
            run_id="changelog-1",
            endpoint_count=1,
            record_count=1,
            event_count=1,
            full_refresh=False,
            scoped_record_count=1,
        )

    monkeypatch.setattr("centric_api.commands.fetch.record_changelog", fake_record_changelog)


def _insert_bundle_item(
    conn,
    run_id: str,
    identity: str,
    archive_path: str,
    revision_id: str,
    sha256: str,
) -> None:
    conn.execute(
        """
        INSERT INTO bundle_items (
            run_id, bundle_name, archive_path, identity, source_endpoint,
            source_record_id, source_label, document_id, revision_id, file_path,
            sha256, bytes, status, change_type, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            "style-bundle",
            archive_path,
            identity,
            "styles",
            "S1",
            "Style One",
            "D1",
            revision_id,
            "/tmp/spec.pdf",
            sha256,
            10,
            "included",
            "added",
            "2026-01-01T00:00:00Z",
        ],
    )


def _insert_applied_raw_file(conn, *, endpoint: str, record_count: int = 0) -> None:
    conn.execute(
        """
        INSERT INTO applied_raw_files (
            file_path, endpoint, source_run_id, is_delta, record_count,
            invalid_record_count, content_sha256, manifest_path, manifest_sha256,
            run_mode, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"/tmp/{endpoint}.jsonl",
            endpoint,
            "run-1",
            0,
            record_count,
            0,
            f"hash-{endpoint}",
            None,
            None,
            "full",
            "2026-01-01T00:00:00Z",
        ],
    )
