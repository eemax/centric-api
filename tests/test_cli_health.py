from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from centric_api.cli import main
from centric_api.store import connect
from tests.helpers_cli import (
    _insert_applied_raw_file,
    _insert_bundle_run,
    _insert_download_run,
    _insert_endpoint_record,
)


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
    assert "Fetch lock:     clear" in output
    assert "Latest Runs" in output
    assert "full  2 endpoints  2 records" in output
    assert "3 events  2 endpoints" in output
    assert "docs  4 downloaded, 0 failed" in output
    assert "style-bundle  1 files" in output
    assert "Data" in output
    assert "Records:          2 current" in output
    assert "Endpoints" in output
    assert "boms" in output
    assert "styles" in output
    assert "- styles:" not in output


def test_status_endpoint_state_fallback_includes_tombstone_only_endpoints(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO endpoint_tombstones (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "boms",
                "B1",
                json.dumps({"id": "B1", "_modified_at": "2026-01-02T00:00:00Z"}),
                "bom-tombstone",
                "2026-01-02T00:00:00Z",
                "boms.jsonl",
                "run-1",
                "2026-01-03T00:00:00Z",
            ],
        )

    exit_code = main(["status", "--db", str(db_path), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["endpoint_state"] == [
        {
            "endpoint": "boms",
            "current_count": 0,
            "tombstone_count": 1,
            "latest_modified_at": "2026-01-02T00:00:00Z",
            "latest_ingested_at": "2026-01-03T00:00:00Z",
        }
    ]


def test_status_reports_swagger_snapshot(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    (home / "swagger.json").write_text(
        json.dumps({"swagger": "2.0", "paths": {"/styles": {"get": {}}}}),
        encoding="utf-8",
    )
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    (home / "swagger.meta.json").write_text(
        json.dumps({"fetched_at": fetched_at}),
        encoding="utf-8",
    )

    exit_code = main(["status", "--db", str(tmp_path / "missing.db"), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["swagger"]["path"] == str(home / "swagger.json")
    assert payload["swagger"]["exists"] is True
    assert payload["swagger"]["meta_exists"] is True
    assert payload["swagger"]["stale"] is False
    assert payload["swagger"]["operation_count"] == 1
    assert payload["swagger"]["endpoint_count"] == 1


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


def test_doctor_warns_when_swagger_is_missing(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    monkeypatch.setenv("CENTRIC_BASE_URL", "https://centric.example.com")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_applied_raw_file(conn, endpoint="documents")
        _insert_applied_raw_file(conn, endpoint="document_revisions")

    exit_code = main(["doctor", "--db", str(db_path), "--json"])

    checks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    swagger_check = next(check for check in checks if check["name"] == "swagger")
    assert exit_code == 0
    assert swagger_check == {
        "status": "WARN",
        "name": "swagger",
        "message": f"Swagger file not found: {home / 'swagger.json'}",
        "repair": "centric-api swagger refresh",
    }


def test_doctor_json_shows_normalized_centric_base_url(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "example-brand")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")

    exit_code = main(["doctor", "--db", str(tmp_path / "missing.db"), "--json"])

    checks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    credentials = next(check for check in checks if check["name"] == "credentials")
    assert exit_code == 1
    assert credentials == {
        "status": "OK",
        "name": "credentials",
        "message": (
            "found credentials for "
            "https://example-brand.centricsoftware.com/csi-requesthandler"
        ),
    }

def test_doctor_human_shows_normalized_centric_base_url(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "example-brand")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")

    exit_code = main(["doctor", "--db", str(tmp_path / "missing.db")])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "credentials" in output
    assert "https://example-brand.centricsoftware.com/csi-requesthandler" in output

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

def test_doctor_reports_stale_dashboard_view_shape(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_BASE_URL", "https://centric.example.com")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")
    db_path = tmp_path / "centric.db"
    with connect(db_path):
        pass
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP VIEW dashboard_recent_changes")

    exit_code = main(["doctor", "--db", str(db_path), "--json"])

    checks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    schema_check = next(check for check in checks if check["name"] == "db_schema_shape")
    assert exit_code == 1
    assert schema_check["status"] == "FAIL"
    assert "missing view dashboard_recent_changes" in schema_check["message"]

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

def test_failed_rebuild_keeps_existing_db_active(tmp_path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S-old",
            payload={"id": "S-old", "_modified_at": "2026-01-01T00:00:00Z"},
            payload_hash="old",
        )
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S-new", "_modified_at": "2026-01-02T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-02T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )

    def fail_changelog(*_args, **_kwargs):
        raise ValueError("forced changelog failure")

    monkeypatch.setattr("centric_api.commands.rebuild_db.record_changelog", fail_changelog)

    exit_code = main(["rebuild-db", "--db", str(db_path), "--raw-dir", str(raw_dir), "--yes"])

    assert exit_code == 1
    assert "forced changelog failure" in capsys.readouterr().err
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT record_id FROM endpoint_records WHERE endpoint = ? ORDER BY record_id",
            ["styles"],
        ).fetchall()
    assert [row[0] for row in rows] == ["S-old"]

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
    assert "Ingesting raw records..." in output
    assert "Updating changelog..." in output
    assert "SQLite Rebuilt" in output
