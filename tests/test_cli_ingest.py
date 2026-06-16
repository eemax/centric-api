from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main


def test_ingest_check_resolves_run_id_and_reports_raw_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_raw_run(tmp_path, "run-1")

    assert main(["ingest", "check", "run-1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["raw_run"]["run_id"] == "run-1"
    assert payload["raw_run"]["mode"] == "full"
    assert payload["files"][0]["endpoint"] == "styles"
    assert payload["files"][0]["line_count"] == 1
    assert payload["files"][0]["applied_state"] == "new"


def test_ingest_raw_run_applies_evidence_and_runs_scoped_changelog(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_raw_run(tmp_path, "run-1")
    db_path = tmp_path / "scratch.db"

    assert (
        main(
            [
                "ingest",
                "raw-run",
                "run-1",
                "--db",
                str(db_path),
                "--changelog",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ingest"]["applied_files"] == 1
    assert payload["ingest"]["records_read"] == 1
    assert payload["ingest"]["records_upserted"] == 1
    assert payload["changelog"]["event_count"] == 1
    assert payload["changelog"]["scoped_record_count"] == 1
    assert payload["changelog_skipped"] is None

    assert (
        main(
            [
                "ingest",
                "raw-run",
                "run-1",
                "--db",
                str(db_path),
                "--changelog",
                "--json",
            ]
        )
        == 0
    )

    skipped_payload = json.loads(capsys.readouterr().out)
    assert skipped_payload["ingest"]["applied_files"] == 0
    assert skipped_payload["ingest"]["skipped_files"] == 1
    assert skipped_payload["changelog"] is None
    assert skipped_payload["changelog_skipped"] == "no current-record changes"

    assert main(["ingest", "check", "run-1", "--db", str(db_path), "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["files"][0]["applied_state"] == "applied"


def test_ingest_check_fails_for_invalid_jsonl(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_raw_run(tmp_path, "run-1")
    (run_dir / "styles.jsonl").write_text("{not-json}\n", encoding="utf-8")

    assert main(["ingest", "check", "run-1", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["files"][0]["invalid_json_lines"] == 1


def test_ingest_raw_run_refuses_invalid_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_raw_run(tmp_path, "run-1")
    (run_dir / "styles.jsonl").write_text("{not-json}\n", encoding="utf-8")

    assert main(["ingest", "raw-run", "run-1", "--db", str(tmp_path / "scratch.db")]) == 1

    captured = capsys.readouterr()
    assert "Raw run check failed for 1 file(s)" in captured.err


def test_ingest_raw_run_refuses_failed_lifecycle_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_raw_run(tmp_path, "run-1")
    failed_dir = tmp_path / "raw" / "failed" / "run-1"
    failed_dir.parent.mkdir(parents=True)
    run_dir.rename(failed_dir)
    (failed_dir / ".completed.json").unlink()
    (failed_dir / ".failed.json").write_text(
        json.dumps({"status": "failed", "run_id": "run-1"}),
        encoding="utf-8",
    )

    assert main(["ingest", "check", str(failed_dir), "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["raw_run"]["lifecycle"] == "failed"

    assert main(["ingest", "raw-run", str(failed_dir), "--db", str(tmp_path / "db.sqlite")]) == 1
    captured = capsys.readouterr()
    assert "Raw run is not completed evidence" in captured.err


def test_ingest_raw_run_refuses_markerless_completed_folder(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_raw_run(tmp_path, "run-1")
    (run_dir / ".completed.json").unlink()

    assert main(["ingest", "check", "run-1", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["raw_run"]["lifecycle"] == "unknown"

    assert main(["ingest", "raw-run", "run-1", "--db", str(tmp_path / "db.sqlite")]) == 1
    captured = capsys.readouterr()
    assert "lifecycle=unknown" in captured.err


def _write_raw_run(tmp_path: Path, run_id: str) -> Path:
    run_dir = tmp_path / "raw" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps(
            {
                "id": "S1",
                "node_name": "Test Style",
                "_modified_at": "2026-01-01T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {
                    "styles": {
                        "file": "styles.jsonl",
                        "is_delta": False,
                        "items_fetched": 1,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (run_dir / ".completed.json").write_text(
        json.dumps({"status": "completed", "run_id": run_id}),
        encoding="utf-8",
    )
    return run_dir
