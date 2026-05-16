from __future__ import annotations

import json

import pytest

from centric_api.cli import (
    _append_cron_event,
    _append_cron_fetch_records,
    _build_parser,
    _parse_jsonl,
    _release_fetch_lock,
    _render_log_line,
    _run_cron_fetch_once,
    _try_acquire_fetch_lock,
    main,
)


def test_cli_help_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "fetch" in output
    assert "changelog" in output
    assert "cron" in output
    assert "download" in output


def test_changelog_summary_empty_db(tmp_path, capsys) -> None:
    exit_code = main(["changelog", "--db", str(tmp_path / "centric.db")])

    assert exit_code == 0
    assert "No changelog events found." in capsys.readouterr().out


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


def test_fetch_log_level_defaults_to_summary() -> None:
    args = _build_parser().parse_args(["fetch"])

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


def test_fetch_lock_helpers_create_and_release_lock(tmp_path) -> None:
    lock_path = tmp_path / "fetch.lock"

    assert _try_acquire_fetch_lock(lock_path) is None
    assert lock_path.is_file()
    assert _try_acquire_fetch_lock(lock_path) is not None

    _release_fetch_lock(lock_path)

    assert not lock_path.exists()


def test_parse_jsonl_preserves_non_json_lines() -> None:
    assert _parse_jsonl('{"status":"ok"}\nnot-json\n') == [
        {"status": "ok"},
        {"record_type": "fetch_stdout", "line": "not-json"},
    ]


def test_cron_log_helpers_write_jsonl_only(tmp_path) -> None:
    log_path = tmp_path / "cron.jsonl"

    _append_cron_event(log_path, record_type="cron_start", schedule="0 * * * *")
    _append_cron_fetch_records(
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

    monkeypatch.setattr("centric_api.cli.run_fetch", fail_fetch)
    args = _build_parser().parse_args(["cron"])
    lock_path = tmp_path / "fetch.lock"
    log_path = tmp_path / "cron.jsonl"

    _run_cron_fetch_once(args, lock_file=lock_path, log_file=log_path)

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["record_type"] == "fetch_stderr"
    assert "boom" in rows[0]["stderr"]
    assert rows[1]["record_type"] == "cron_fetch_summary"
    assert rows[1]["exit_code"] == 1
    assert not lock_path.exists()


def test_cron_fetch_skips_when_fetch_lock_exists(tmp_path) -> None:
    args = _build_parser().parse_args(["cron"])
    lock_path = tmp_path / "fetch.lock"
    log_path = tmp_path / "cron.jsonl"
    lock_path.write_text("locked", encoding="utf-8")

    _run_cron_fetch_once(args, lock_file=lock_path, log_file=log_path)

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
