from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from centric_api.fetch_common import FetchError, _request_json_with_retry
from centric_api.fetcher import run_endpoint
from centric_api.models import CountSpec, EndpointSpec, FetcherConfig


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


def test_fetcher_marks_checkpoint_for_restart_on_count_mismatch(tmp_path: Path) -> None:
    cfg = _fetcher_config(tmp_path)
    auth = _PagedAuth(count=3, pages={0: [{"id": "S1"}, {"id": "S2"}]})

    with pytest.raises(FetchError, match="expected count to match actual count"):
        run_endpoint(_endpoint(limit=2), auth, cfg)

    checkpoint = json.loads((cfg.checkpoint_dir / "styles.json").read_text(encoding="utf-8"))
    assert checkpoint["restart_from_zero"] is True
    assert checkpoint["next_skip"] == 0
    assert checkpoint["fetched_count"] == 0


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
