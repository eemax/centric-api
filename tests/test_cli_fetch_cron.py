from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from centric_api.changelog import ChangelogRun
from centric_api.cli import main
from centric_api.cli_parser import build_parser
from centric_api.commands.common import (
    append_cron_log_event,
    append_cron_log_fetch_records,
    release_fetch_lock,
    try_acquire_fetch_lock,
)
from centric_api.commands.cron import run_cron_fetch_once
from centric_api.commands.fetch import _allocate_run_id, run_fetch
from centric_api.fetch_common import FetchError
from centric_api.models import (
    AuthSettings,
    CountSpec,
    EndpointSpec,
    FetcherConfig,
    FetchProgressEvent,
    FetchRunResult,
)
from centric_api.rendering.fetch import print_human_fetch_summary
from centric_api.rendering.logs import render_log_line
from centric_api.runtime_io import parse_jsonl
from centric_api.store import IngestResult
from tests.helpers_cli import _patch_fetch_pipeline


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

def test_fetch_log_renderer_includes_failed_request_url() -> None:
    line = render_log_line(
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "level": "summary",
            "event": "request_failed",
            "endpoint": "styles",
            "request_kind": "data fetch",
            "method": "GET",
            "url": "https://centric.example.com/api/v2/styles?skip=50&limit=50",
            "reason": "non_retryable_http_status",
            "status_code": 400,
            "attempt": 1,
            "max_attempts": 3,
        }
    )

    assert line == (
        "2026-01-01T00:00:01Z REQUEST failed endpoint=styles "
        "request_kind=\"data fetch\" method=GET "
        "url=https://centric.example.com/api/v2/styles?skip=50&limit=50 "
        "reason=non_retryable_http_status status_code=400 attempt=1 max_attempts=3"
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
    assert "Fetch complete" in captured.out
    assert "Warnings" in captured.out
    assert "Validation" in captured.out
    assert "Fetch run" in captured.err
    assert "Pipeline" in captured.err
    assert "ingest=running" in captured.err
    assert "ingest=ok records_read=1 upserts=1 deletes=0" in captured.err
    assert "changelog=running" in captured.err
    assert "changelog=ok events=1 scoped=1" in captured.err
    assert "pipeline=done ingest=ok changelog=ok elapsed=" in captured.err
    delta_state = yaml.safe_load((tmp_path / "delta.yml").read_text(encoding="utf-8"))
    endpoint_state = delta_state["endpoints"]["styles"]
    assert endpoint_state["last_attempted_status"] == "OK"
    assert "last_successful_fetch_start" in endpoint_state
    assert "last_successful_fetch_end" in endpoint_state

def test_fetch_delta_progress_reports_missing_floor_reason(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    def fake_run_endpoint(*_args, progress_callback=None, delta_floor_reason=None, **_kwargs):
        if progress_callback is not None:
            progress_callback(
                FetchProgressEvent(
                    kind="endpoint_start",
                    endpoint="styles",
                    delta_floor_reason=delta_floor_reason,
                    limit=50,
                    start_skip=0,
                    expected_count=1,
                    retries_used=0,
                    elapsed_seconds=0.0,
                )
            )
        return FetchRunResult(
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
        )

    monkeypatch.setattr("centric_api.commands.fetch.run_endpoint", fake_run_endpoint)

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[styles] START" in captured.err
    assert "delta_floor=none" in captured.err
    assert "reason=delta_state_missing" in captured.err

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
    assert "Fetch run" not in captured.err
    assert "Pipeline" not in captured.err
    assert "changelog=running" not in captured.err

def test_fetch_warning_propagates_to_json_manifest_delta_and_logs(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "centric_api.commands.fetch.run_endpoint",
        lambda *_args, **_kwargs: FetchRunResult(
            endpoint="styles",
            pages_fetched=3,
            items_fetched=1499,
            expected_count=1498,
            retries_used=0,
            start_skip=0,
            next_skip=1500,
            duration_seconds=1.2,
            output_file=tmp_path / "raw" / "styles.jsonl",
            checkpoint_file=tmp_path / "checkpoints" / "styles.json",
            warnings=["count drift"],
            count_validation_status="warning",
            count_validation_reason="count drift",
            id_validation_checked_items=1499,
            id_validation_unique_ids=1499,
        ),
    )

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db"), "--json"])

    captured = capsys.readouterr()
    records = parse_jsonl(captured.out)
    endpoint_record = next(record for record in records if record.get("endpoint") == "styles")
    pipeline_record = next(
        record for record in records if record.get("record_type") == "pipeline_summary"
    )
    manifest = json.loads(Path(pipeline_record["manifest"]).read_text(encoding="utf-8"))
    delta_state = yaml.safe_load((tmp_path / "delta.yml").read_text(encoding="utf-8"))
    log_text = (tmp_path / "logs" / "fetch.log").read_text(encoding="utf-8")

    assert exit_code == 0
    assert endpoint_record["status"] == "warn"
    assert endpoint_record["warnings_count"] == 1
    assert endpoint_record["count_validation"] == "warning"
    assert pipeline_record["status"] == "warn"
    assert pipeline_record["endpoints_ok"] == 0
    assert pipeline_record["endpoints_warn"] == 1
    assert pipeline_record["endpoints_failed"] == 0
    assert manifest["status"] == "WARN"
    assert manifest["endpoints_ok"] == 0
    assert manifest["endpoints_warn"] == 1
    assert manifest["endpoints"]["styles"]["status"] == "WARN"
    assert manifest["endpoints"]["styles"]["warnings_count"] == 1
    assert manifest["endpoints"]["styles"]["count_validation"] == "warning"
    assert delta_state["endpoints"]["styles"]["last_attempted_status"] == "WARN"
    assert "last_successful_fetch_start" in delta_state["endpoints"]["styles"]
    assert "ENDPOINT warn" in log_text
    assert "RUN warn" in log_text

def test_fetch_does_not_advance_delta_success_on_pipeline_failure(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    def fail_ingest(*_args, **_kwargs):
        raise ValueError("store is unavailable")

    monkeypatch.setattr("centric_api.commands.fetch.ingest_raw_dir", fail_ingest)

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db")])

    assert exit_code == 1
    assert "ingest failed: store is unavailable" in capsys.readouterr().out
    delta_state = yaml.safe_load((tmp_path / "delta.yml").read_text(encoding="utf-8"))
    endpoint_state = delta_state["endpoints"]["styles"]
    assert endpoint_state["last_attempted_status"] == "PIPELINE_FAILED"
    assert endpoint_state["last_attempted_error"] == "ingest failed: store is unavailable"
    assert "last_successful_fetch_start" not in endpoint_state
    assert "last_successful_fetch_end" not in endpoint_state

def test_fetch_failure_reports_elapsed_and_log_path(tmp_path, monkeypatch, capsys) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    def fail_endpoint(*_args, **_kwargs):
        raise FetchError("HTTP 429 Too Many Requests")

    monkeypatch.setattr("centric_api.commands.fetch.run_endpoint", fail_endpoint)

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[styles] ERROR  elapsed=" in captured.err
    assert "delta_floor=none" in captured.err
    assert "reason=delta_state_missing" in captured.err
    assert "HTTP 429 Too Many Requests" in captured.err
    assert "Fetch finished with failures" in captured.out
    assert f"Log: {tmp_path / 'logs' / 'fetch.log'}" in captured.out

def test_fetch_quiet_suppresses_progress_but_reports_errors(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _patch_fetch_pipeline(monkeypatch, tmp_path)

    def fail_endpoint(*_args, **_kwargs):
        raise FetchError("HTTP 429 Too Many Requests")

    monkeypatch.setattr("centric_api.commands.fetch.run_endpoint", fail_endpoint)

    exit_code = main(["fetch", "--quiet", "--db", str(tmp_path / "centric.db")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "[styles] ERROR  elapsed=" in captured.err
    assert "HTTP 429 Too Many Requests" in captured.err
    assert "Fetch run" not in captured.err
    assert "Pipeline" not in captured.err
    assert "Fetch result" not in captured.err

def test_fetch_partial_result_reports_partial_status(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    endpoints = [
        EndpointSpec(
            name="styles",
            api_version="v2",
            path="styles",
            count_spec=CountSpec(path="count/Style"),
        ),
        EndpointSpec(
            name="boms",
            api_version="v2",
            path="boms",
            count_spec=CountSpec(path="count/BOM"),
        ),
    ]
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.load_fetcher_settings",
        lambda _path: (fetcher_cfg, AuthSettings(timeout=1), endpoints),
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

    def fake_run_endpoint(spec, *_args, **_kwargs):
        if spec.name == "boms":
            raise FetchError("HTTP 400 Bad Request")
        return FetchRunResult(
            endpoint="styles",
            pages_fetched=2,
            items_fetched=100,
            expected_count=100,
            retries_used=0,
            start_skip=0,
            next_skip=100,
            duration_seconds=1.2,
            output_file=tmp_path / "raw" / "styles.jsonl",
            checkpoint_file=tmp_path / "checkpoints" / "styles.json",
            id_validation_checked_items=100,
            id_validation_unique_ids=100,
        )

    monkeypatch.setattr("centric_api.commands.fetch.run_endpoint", fake_run_endpoint)
    monkeypatch.setattr(
        "centric_api.commands.fetch.ingest_raw_dir",
        lambda *_args, **_kwargs: IngestResult(
            applied_files=1,
            skipped_files=0,
            records_read=100,
            records_upserted=100,
            records_deleted=0,
            records_hard_deleted=0,
            invalid_records=0,
            endpoints={"styles": 100},
            upserted_record_ids_by_endpoint={"styles": ("S1",)},
            deleted_record_ids_by_endpoint={},
            deleted_record_delete_types_by_endpoint={},
        ),
    )
    monkeypatch.setattr(
        "centric_api.commands.pipeline.record_changelog",
        lambda *_args, **_kwargs: ChangelogRun(
            run_id="changelog-1",
            endpoint_count=1,
            record_count=100,
            event_count=1,
            full_refresh=False,
            scoped_record_count=100,
        ),
    )

    exit_code = main(["fetch", "--db", str(tmp_path / "centric.db")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[boms] ERROR  elapsed=" in captured.err
    assert (
        "status=partial endpoints=1 ok, 0 warn, 1 failed, 2 total "
        "records=100 pages=2 retries=0 elapsed="
    ) in captured.err
    assert "Fetch finished with failures" in captured.out
    assert "boms" in captured.out
    assert "failed" in captured.out
    assert "- boms\n  HTTP 400 Bad Request" in captured.out

def test_fetch_summary_shows_count_drift_warning(tmp_path, capsys) -> None:
    print_human_fetch_summary(
        mode="delta",
        run_id="run-1",
        raw_dir=tmp_path / "raw",
        selected_count=3,
        results=[
            FetchRunResult(
                endpoint="styles",
                pages_fetched=2,
                items_fetched=100,
                expected_count=100,
                retries_used=0,
                start_skip=0,
                next_skip=100,
                duration_seconds=1.0,
                output_file=tmp_path / "raw" / "styles.jsonl",
                checkpoint_file=tmp_path / "checkpoints" / "styles.json",
                id_validation_checked_items=100,
                id_validation_unique_ids=100,
            ),
            FetchRunResult(
                endpoint="size_charts",
                pages_fetched=3,
                items_fetched=1499,
                expected_count=1498,
                retries_used=0,
                start_skip=0,
                next_skip=1500,
                duration_seconds=1.2,
                output_file=tmp_path / "raw" / "size_charts.jsonl",
                checkpoint_file=tmp_path / "checkpoints" / "size_charts.json",
                warnings=["count drift"],
                count_validation_status="warning",
                count_validation_reason="count drift",
                id_validation_checked_items=1499,
                id_validation_unique_ids=1499,
            )
        ],
        failures=[
            (
                "size_chart_revisions",
                "Data pagination ended early for 'size_chart_revisions': last page "
                "skip=1000 limit=500 returned 0 items after fetching 1000 of expected 1001.",
            )
        ],
        duration_seconds=1.2,
        ingest_result=None,
        changelog_run=None,
        changelog_skipped=None,
        pipeline_error=None,
    )

    output = capsys.readouterr().out
    assert "Fetch finished with failures" in output
    assert "Endpoints: 1 ok, 1 warn, 1 failed, 3 total" in output
    assert "Count Diff" in output
    assert "Diff %" in output
    assert "styles" in output
    assert "size_charts" in output
    assert "warn" in output
    styles_row = next(line for line in output.splitlines() if line.startswith("styles"))
    styles_columns = styles_row.split()
    assert styles_columns[4:6] == ["0", "-"]
    assert "+1 over" in output
    assert "+0.067%" in output
    assert "size_chart_revisions" in output
    assert "failed" in output
    assert "-1 under" in output
    assert "-0.100%" in output

def test_fetch_summary_title_shows_warning_without_failures(tmp_path, capsys) -> None:
    print_human_fetch_summary(
        mode="delta",
        run_id="run-1",
        raw_dir=tmp_path / "raw",
        selected_count=1,
        results=[
            FetchRunResult(
                endpoint="size_charts",
                pages_fetched=3,
                items_fetched=1499,
                expected_count=1498,
                retries_used=0,
                start_skip=0,
                next_skip=1500,
                duration_seconds=1.2,
                output_file=tmp_path / "raw" / "size_charts.jsonl",
                checkpoint_file=tmp_path / "checkpoints" / "size_charts.json",
                warnings=["count drift"],
                count_validation_status="warning",
                count_validation_reason="count drift",
                id_validation_checked_items=1499,
                id_validation_unique_ids=1499,
            )
        ],
        failures=[],
        duration_seconds=1.2,
        ingest_result=None,
        changelog_run=None,
        changelog_skipped=None,
        pipeline_error=None,
    )

    output = capsys.readouterr().out
    assert output.startswith("Fetch complete with warnings")
    assert "Endpoints: 0 ok, 1 warn, 0 failed, 1 total" in output

def test_fetch_run_id_suffixes_existing_run_dirs(tmp_path) -> None:
    raw_root = tmp_path / "raw"
    existing = raw_root / "runs" / "2026-01-01T000000Z-delta"
    existing.mkdir(parents=True)

    run_id = _allocate_run_id(
        raw_root,
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        "delta",
        None,
    )

    assert run_id == "2026-01-01T000000Z-delta-2"

def test_fetch_lock_helpers_create_and_release_lock(tmp_path) -> None:
    lock_path = tmp_path / "fetch.lock"

    assert try_acquire_fetch_lock(lock_path) is None
    assert lock_path.is_file()
    assert try_acquire_fetch_lock(lock_path) is not None

    release_fetch_lock(lock_path)

    assert not lock_path.exists()

def test_fetch_interrupt_releases_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))

    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("centric_api.commands.fetch._run_fetch_unlocked", interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_fetch(
            argparse.Namespace(
                delta_dry_run=False,
                skip_fetch_lock=False,
            )
        )

    assert not (tmp_path / "fetch.lock").exists()

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
