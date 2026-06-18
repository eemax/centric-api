from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from centric_api.fetch_checkpoint import write_checkpoint
from centric_api.fetch_common import (
    FetchError,
    _compile_query_params,
    _request_json_with_retry,
    _with_pagination_params,
)
from centric_api.fetch_delta_state import write_delta_state
from centric_api.fetch_manifest import write_run_manifest
from centric_api.fetcher import run_endpoint
from centric_api.models import CountSpec, EndpointSpec, FetcherConfig, FetchRunResult


def test_fetcher_fetches_pages_and_writes_completed_checkpoint(tmp_path: Path) -> None:
    auth = _PagedAuth(
        count=3,
        pages={
            0: [{"id": "S1"}, {"id": "S2"}],
            2: [{"id": "S3"}],
        },
    )

    result = run_endpoint(_endpoint(limit=2), auth, _fetcher_config(tmp_path))

    assert result.items_fetched == 3
    assert result.pages_fetched == 2
    assert [json.loads(line) for line in result.output_file.read_text().splitlines()] == [
        {"id": "S1"},
        {"id": "S2"},
        {"id": "S3"},
    ]
    checkpoint = json.loads(result.checkpoint_file.read_text(encoding="utf-8"))
    assert checkpoint["completed"] is True
    assert checkpoint["fetched_count"] == 3
    assert checkpoint["restart_from_zero"] is False


def test_fetcher_resume_seeds_existing_window_and_appends_remaining_page(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    output_path = cfg.output_dir / "styles.jsonl"
    checkpoint_path = cfg.checkpoint_dir / "styles.json"
    output_path.parent.mkdir(parents=True)
    checkpoint_path.parent.mkdir(parents=True)
    output_path.write_text('{"id":"S1"}\n{"id":"S2"}\n', encoding="utf-8")
    checkpoint_path.write_text(
        json.dumps(
            {
                "endpoint": "styles",
                "next_skip": 2,
                "fetched_count": 2,
                "completed": False,
                "restart_from_zero": False,
                "window_start_line": 0,
                "output_file": str(output_path),
            }
        ),
        encoding="utf-8",
    )
    auth = _PagedAuth(count=4, pages={2: [{"id": "S3"}, {"id": "S4"}]})

    result = run_endpoint(_endpoint(limit=2), auth, cfg, resume=True)

    assert result.start_skip == 2
    assert result.items_fetched == 4
    assert [json.loads(line) for line in output_path.read_text().splitlines()] == [
        {"id": "S1"},
        {"id": "S2"},
        {"id": "S3"},
        {"id": "S4"},
    ]


def test_fetcher_resume_truncates_uncheckpointed_output_tail(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    output_path = cfg.output_dir / "styles.jsonl"
    checkpoint_path = cfg.checkpoint_dir / "styles.json"
    output_path.parent.mkdir(parents=True)
    checkpoint_path.parent.mkdir(parents=True)
    output_path.write_text('{"id":"S1"}\n{"id":"S2"}\n{"id":"STALE"}\n', encoding="utf-8")
    checkpoint_path.write_text(
        json.dumps(
            {
                "endpoint": "styles",
                "next_skip": 2,
                "fetched_count": 2,
                "completed": False,
                "restart_from_zero": False,
                "window_start_line": 0,
                "output_file": str(output_path),
            }
        ),
        encoding="utf-8",
    )
    auth = _PagedAuth(count=4, pages={2: [{"id": "S3"}, {"id": "S4"}]})

    result = run_endpoint(_endpoint(limit=2), auth, cfg, resume=True)

    assert result.items_fetched == 4
    assert [json.loads(line) for line in output_path.read_text().splitlines()] == [
        {"id": "S1"},
        {"id": "S2"},
        {"id": "S3"},
        {"id": "S4"},
    ]


def test_fetcher_marks_checkpoint_for_restart_on_count_mismatch(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(count=3, pages={0: [{"id": "S1"}, {"id": "S2"}]})

    with pytest.raises(FetchError, match="Data pagination ended early"):
        run_endpoint(_endpoint(limit=2), auth, cfg)

    checkpoint = json.loads((cfg.checkpoint_dir / "styles.json").read_text(encoding="utf-8"))
    assert checkpoint["restart_from_zero"] is True
    assert checkpoint["next_skip"] == 0
    assert checkpoint["fetched_count"] == 0


def test_fetcher_accepts_tiny_count_drift_on_large_endpoint(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(
        count=10001,
        pages={
            0: [{"id": f"S{i}"} for i in range(5000)],
            5000: [{"id": f"S{i}"} for i in range(5000, 10000)],
            10000: [],
        },
    )

    result = run_endpoint(_endpoint(limit=5000), auth, cfg)

    assert result.items_fetched == 10000
    assert result.expected_count == 10001
    assert result.count_validation_status == "warning"
    assert result.count_validation_reason is not None
    assert "Accepted as small count drift" in result.count_validation_reason
    checkpoint = json.loads((cfg.checkpoint_dir / "styles.json").read_text(encoding="utf-8"))
    assert checkpoint["completed"] is True
    assert checkpoint["restart_from_zero"] is False


def test_fetcher_accepts_tiny_overfetch_count_drift_on_last_page(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(
        count=14998,
        pages={
            0: [{"id": f"S{i}"} for i in range(5000)],
            5000: [{"id": f"S{i}"} for i in range(5000, 10000)],
            10000: [{"id": f"S{i}"} for i in range(10000, 14999)],
        },
    )

    result = run_endpoint(_endpoint(limit=5000), auth, cfg)

    assert result.items_fetched == 14999
    assert result.expected_count == 14998
    assert result.count_validation_status == "warning"
    assert result.count_validation_reason is not None
    assert "Accepted as small count drift" in result.count_validation_reason


def test_fetcher_rejects_count_drift_above_absolute_cap(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(
        count=20000,
        pages={
            0: [{"id": f"S{i}"} for i in range(5000)],
            5000: [{"id": f"S{i}"} for i in range(5000, 10000)],
            10000: [{"id": f"S{i}"} for i in range(10000, 15000)],
            15000: [{"id": f"S{i}"} for i in range(15000, 19989)],
        },
    )

    with pytest.raises(FetchError, match="fetching 19989 of expected 20000"):
        run_endpoint(_endpoint(limit=5000), auth, cfg)


def test_fetcher_rejects_count_drift_above_relative_tolerance(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(
        count=3000,
        pages={
            0: [{"id": f"S{i}"} for i in range(1000)],
            1000: [{"id": f"S{i}"} for i in range(1000, 2000)],
            2000: [{"id": f"S{i}"} for i in range(2000, 2999)],
        },
    )

    with pytest.raises(FetchError, match="fetching 2999 of expected 3000"):
        run_endpoint(_endpoint(limit=1000), auth, cfg)


def test_fetcher_rejects_tiny_overfetch_count_drift_on_full_page(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(
        count=999,
        pages={
            0: [{"id": f"S{i}"} for i in range(500)],
            500: [{"id": f"S{i}"} for i in range(500, 1000)],
        },
    )

    with pytest.raises(FetchError, match="Fetched 1000 items"):
        run_endpoint(_endpoint(limit=500), auth, cfg)


def test_fetcher_rejects_duplicate_ids(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(count=2, pages={0: [{"id": "S1"}, {"id": "S1"}]})

    with pytest.raises(FetchError, match="duplicate ids=1"):
        run_endpoint(_endpoint(limit=2), auth, cfg)

    checkpoint = json.loads((cfg.checkpoint_dir / "styles.json").read_text(encoding="utf-8"))
    assert checkpoint["restart_from_zero"] is True


def test_request_json_retries_transient_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("centric_api.fetch_common.time.sleep", sleeps.append)
    auth = _RetryAuth(
        [httpx.Response(503, json={"error": "busy"}), httpx.Response(200, json={"ok": True})]
    )
    retries = [0]

    payload = _request_json_with_retry(
        auth,
        method="GET",
        url="https://centric.example.com/api/v2/styles",
        params=None,
        fetcher_cfg=FetcherConfig(
            output_dir=Path("raw"),
            checkpoint_dir=Path("checkpoints"),
            retry_base_seconds=0.25,
            retry_max_seconds=1,
            retry_max_attempts=2,
        ),
        retries_used_ref=retries,
    )

    assert payload == {"ok": True}
    assert retries == [1]
    assert sleeps == [0.25]


def test_request_json_retries_http_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("centric_api.fetch_common.time.sleep", sleeps.append)
    auth = _RetryAuth(
        [httpx.Response(408, json={"error": "timeout"}), httpx.Response(200, json={"ok": True})]
    )
    retries = [0]

    payload = _request_json_with_retry(
        auth,
        method="GET",
        url="https://centric.example.com/api/v2/styles",
        params=None,
        fetcher_cfg=FetcherConfig(
            output_dir=Path("raw"),
            checkpoint_dir=Path("checkpoints"),
            retry_base_seconds=0.25,
            retry_max_seconds=1,
            retry_max_attempts=2,
        ),
        retries_used_ref=retries,
    )

    assert payload == {"ok": True}
    assert retries == [1]
    assert sleeps == [0.25]


def test_request_json_does_not_retry_non_retryable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("centric_api.fetch_common.time.sleep", sleeps.append)
    auth = _RetryAuth([httpx.Response(400, json={"error": "bad filter"})])

    with pytest.raises(FetchError, match="non-retryable HTTP 400"):
        _request_json_with_retry(
            auth,
            method="GET",
            url="https://centric.example.com/api/v2/styles",
            params=None,
            fetcher_cfg=FetcherConfig(
                output_dir=Path("raw"),
                checkpoint_dir=Path("checkpoints"),
                retry_max_attempts=3,
            ),
            retries_used_ref=[0],
        )

    assert auth.calls == 1
    assert sleeps == []


def test_request_json_retries_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("centric_api.fetch_common.time.sleep", sleeps.append)
    auth = _RetryAuth([httpx.ConnectError("network down"), httpx.Response(200, json={"ok": True})])

    payload = _request_json_with_retry(
        auth,
        method="GET",
        url="https://centric.example.com/api/v2/styles",
        params=None,
        fetcher_cfg=FetcherConfig(
            output_dir=Path("raw"),
            checkpoint_dir=Path("checkpoints"),
            retry_base_seconds=0.5,
            retry_max_attempts=2,
        ),
        retries_used_ref=[0],
    )

    assert payload == {"ok": True}
    assert sleeps == [0.5]


def test_request_json_raises_after_retry_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("centric_api.fetch_common.time.sleep", sleeps.append)
    auth = _RetryAuth(
        [
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(503, json={"error": "still busy"}),
        ]
    )

    with pytest.raises(FetchError, match="failed after retries"):
        _request_json_with_retry(
            auth,
            method="GET",
            url="https://centric.example.com/api/v2/styles",
            params=None,
            fetcher_cfg=FetcherConfig(
                output_dir=Path("raw"),
                checkpoint_dir=Path("checkpoints"),
                retry_base_seconds=0.5,
                retry_max_attempts=2,
            ),
            retries_used_ref=[0],
        )

    assert auth.calls == 2
    assert sleeps == [0.5]


def test_request_json_rejects_invalid_json_response() -> None:
    auth = _RetryAuth([httpx.Response(200, content=b"not json")])

    with pytest.raises(FetchError, match="Failed to parse JSON response"):
        _request_json_with_retry(
            auth,
            method="GET",
            url="https://centric.example.com/api/v2/styles",
            params=None,
            fetcher_cfg=FetcherConfig(output_dir=Path("raw"), checkpoint_dir=Path("checkpoints")),
            retries_used_ref=[0],
        )


def test_compile_query_params_normalizes_operators_and_decoded() -> None:
    params = _compile_query_params(
        {
            "_modified_at=ge": "2026-01-01T00:00:00Z",
            "active=": True,
            "decoded": False,
            "node_name": "Style One",
        }
    )

    assert params == [
        ("_modified_at", "ge2026-01-01T00:00:00Z"),
        ("active", True),
        ("decoded", False),
        ("node_name", "Style One"),
    ]


def test_compile_query_params_adds_decoded_when_missing() -> None:
    assert _compile_query_params({"active": True}) == [("active", True), ("decoded", True)]


def test_pagination_params_replace_existing_skip_and_limit() -> None:
    params = _with_pagination_params(
        [("active", True), ("skip", 999), ("limit", 999), ("decoded", True)],
        skip=50,
        limit=25,
    )

    assert params == [("active", True), ("decoded", True), ("skip", 50), ("limit", 25)]


def test_write_checkpoint_cleans_temp_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(self: Path, _target: Path) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    checkpoint_path = tmp_path / "checkpoints" / "styles.json"

    with pytest.raises(RuntimeError, match="replace failed"):
        write_checkpoint(checkpoint_path, "styles", 0, 0)

    assert not checkpoint_path.exists()
    assert not (checkpoint_path.parent / ".styles.json.tmp").exists()


def test_write_delta_state_cleans_temp_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(self: Path, _target: Path) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    state_path = tmp_path / "state" / "delta.yml"

    with pytest.raises(RuntimeError, match="replace failed"):
        write_delta_state(state_path, {"version": 1, "endpoints": {}})

    assert not state_path.exists()
    assert not (state_path.parent / ".delta.yml.tmp").exists()


def test_write_run_manifest_cleans_temp_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(self: Path, _target: Path) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    output_dir = tmp_path / "raw" / "runs" / "run1"
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    finished_at = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="replace failed"):
        write_run_manifest(
            output_dir=output_dir,
            run_id="run1",
            mode="delta",
            run_started_at=started_at,
            run_finished_at=finished_at,
            selected_specs=[],
            results=[],
            failures=[],
            endpoint_records=[],
            modified_since=None,
            utc_iso=lambda value: value.isoformat(),
        )

    assert not (output_dir / "manifest.json").exists()
    assert not (output_dir / ".manifest.json.tmp").exists()


def test_write_run_manifest_adds_raw_index_metadata(tmp_path: Path) -> None:
    output_dir = tmp_path / "raw" / "runs" / "run1"
    output_dir.mkdir(parents=True)
    raw_path = output_dir / "styles.delta.jsonl"
    raw_path.write_text(
        json.dumps({"id": "S1", "node_name": "Style"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    finished_at = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)

    manifest_path = write_run_manifest(
        output_dir=output_dir,
        run_id="run1",
        mode="delta",
        run_started_at=started_at,
        run_finished_at=finished_at,
        selected_specs=[_endpoint(limit=50)],
        results=[
            FetchRunResult(
                endpoint="styles",
                pages_fetched=1,
                items_fetched=1,
                expected_count=1,
                retries_used=0,
                start_skip=0,
                next_skip=50,
                duration_seconds=0.1,
                output_file=raw_path,
                checkpoint_file=tmp_path / "checkpoint.json",
            )
        ],
        failures=[],
        endpoint_records=[
            {
                "endpoint": "styles",
                "file": "styles.delta.jsonl",
                "items_fetched": 1,
            }
        ],
        modified_since=None,
        utc_iso=lambda value: value.isoformat(),
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    endpoint = manifest["endpoints"]["styles"]
    assert endpoint["index_file"] == "styles.delta.index.jsonl"
    assert endpoint["record_count"] == 1
    assert endpoint["line_count"] == 1
    assert endpoint["byte_size"] == raw_path.stat().st_size
    assert len(endpoint["content_sha256"]) == 64
    assert len(endpoint["index_sha256"]) == 64
    assert (output_dir / "styles.delta.index.jsonl").is_file()


def _endpoint(*, limit: int) -> EndpointSpec:
    return EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        limit=limit,
        count_spec=CountSpec(path="count/Style"),
    )


def _fetcher_config(tmp_path: Path) -> FetcherConfig:
    return FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
        retry_max_attempts=1,
    )


class _PagedAuth:
    base_url = "https://centric.example.com"

    def __init__(self, *, count: int, pages: dict[int, list[dict[str, Any]]]) -> None:
        self.count = count
        self.pages = pages

    def request(self, _method: str, url: str, *, params: list[tuple[str, Any]] | None = None):
        if "/count/" in url:
            return _JsonResponse({"count": self.count})
        skip = dict(params or []).get("skip", 0)
        return _JsonResponse(self.pages.get(skip, []))


class _RetryAuth:
    base_url = "https://centric.example.com"

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.calls = 0

    def request(self, *_args, **_kwargs) -> httpx.Response:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class _JsonResponse:
    status_code = 200
    reason_phrase = "OK"
    headers = {"content-type": "application/json"}

    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload
