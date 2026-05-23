from __future__ import annotations

import json
import sqlite3

import pytest

from centric_api.changelog import record_changelog
from centric_api.cli import main
from centric_api.cli_output import _render_log_line
from centric_api.cli_parser import build_parser
from centric_api.commands.common import (
    append_cron_log_event,
    append_cron_log_fetch_records,
    release_fetch_lock,
    try_acquire_fetch_lock,
)
from centric_api.commands.cron import run_cron_fetch_once
from centric_api.runtime_io import parse_jsonl
from centric_api.store import connect


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


def test_changelog_summary_empty_db(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    exit_code = main(["changelog", "--db", str(db_path)])

    assert exit_code == 0
    assert "No changelog events found." in capsys.readouterr().out
    assert not db_path.exists()


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
    run_line = _render_log_line(
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
    endpoint_line = _render_log_line(
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
    assert "Fetch:      2026-01-01T00:00:00Z  full  2 endpoints  2 records" in output
    assert "Changelog:  2026-01-02T00:00:00Z  3 events  2 endpoints" in output
    assert "Download:   2026-01-02T00:00:00Z  docs  4 downloaded, 0 failed" in output
    assert "Bundle:     2026-01-02T00:00:00Z  style-bundle  1 files" in output
    assert "Data" in output
    assert "Records:          2 current" in output
    assert "Latest modified:  2026-01-02T00:00:00Z" in output
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
    payload = json.loads(capsys.readouterr().out)
    assert payload["ingest"]["records_read"] == 1
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM endpoint_records").fetchone()[0]
    assert count == 1


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
