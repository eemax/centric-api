from __future__ import annotations

import json
from pathlib import Path

import pytest

from centric_api.config import ConfigError, load_fetcher_settings
from centric_api.fetch_common import FetchError
from centric_api.fetch_pagination import get_expected_count
from centric_api.fetcher import run_endpoint
from centric_api.models import CountSpec, EndpointSpec, FetcherConfig


class _JsonResponse:
    status_code = 200
    reason_phrase = "OK"
    headers = {"content-type": "application/json"}

    def __init__(self, payload: dict):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_load_fetcher_settings_runtime_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )

    fetcher_cfg, auth_settings, endpoints = load_fetcher_settings(config)

    assert fetcher_cfg.output_dir == tmp_path / "home" / "raw"
    assert fetcher_cfg.checkpoint_dir == tmp_path / "home" / "checkpoints"
    assert auth_settings.env_file == tmp_path / "home" / "local.env"
    assert [endpoint.name for endpoint in endpoints] == ["styles"]
    assert endpoints[0].count_spec.path == "count/Style"


def test_load_fetcher_settings_expands_user_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = home / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )

    fetcher_cfg, _auth_settings, endpoints = load_fetcher_settings("~/fetcher.yml")

    assert fetcher_cfg.timeout == 5
    assert [endpoint.name for endpoint in endpoints] == ["styles"]


def test_load_fetcher_settings_expands_user_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
env_file: ~/centric.env
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )

    _fetcher_cfg, auth_settings, _endpoints = load_fetcher_settings(config)

    assert auth_settings.env_file == home / "centric.env"


def test_load_fetcher_settings_requires_count_spec(tmp_path: Path) -> None:
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="count_spec"):
        load_fetcher_settings(config)


def test_load_fetcher_settings_rejects_unknown_keys(tmp_path: Path) -> None:
    root_config = tmp_path / "fetcher-root.yml"
    root_config.write_text(
        """
timeout: 5
typo: nope
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )
    endpoint_config = tmp_path / "fetcher-endpoint.yml"
    endpoint_config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    typo_endpoint: nope
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )
    count_config = tmp_path / "fetcher-count.yml"
    count_config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
      typo_count: nope
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="fetcher config has unknown keys: typo"):
        load_fetcher_settings(root_config)
    with pytest.raises(ConfigError, match="endpoint has unknown keys: typo_endpoint"):
        load_fetcher_settings(endpoint_config)
    with pytest.raises(ConfigError, match="count_spec has unknown keys: typo_count"):
        load_fetcher_settings(count_config)


def test_count_preflight_rejects_fractional_counts(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

    class Response:
        status_code = 200
        reason_phrase = "OK"
        text = '{"count": 1.5}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"count": 1.5}

    def request(*_args, **_kwargs):
        return Response()

    auth = Auth()
    auth.request = request
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )
    fetcher_cfg, _auth_settings, endpoints = load_fetcher_settings(config)

    with pytest.raises(FetchError, match="non-integer"):
        get_expected_count(endpoints[0], auth, fetcher_cfg)


def test_summary_request_failure_logs_exact_request_url(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

        def request(self, *_args, **_kwargs):
            class Response:
                status_code = 400
                reason_phrase = "Bad Request"
                text = '{"error": "bad filter"}'
                headers = {"content-type": "application/json"}

            return Response()

    spec = EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        count_spec=CountSpec(path="count/Style", query_params={"foo": "bar"}),
    )
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    events = []

    with pytest.raises(FetchError, match="non-retryable HTTP 400"):
        get_expected_count(spec, Auth(), fetcher_cfg, api_log_callback=events.append)

    request_failed = next(event for event in events if event.get("event") == "request_failed")
    assert request_failed["level"] == "summary"
    assert request_failed["endpoint"] == "styles"
    assert request_failed["request_kind"] == "count preflight"
    assert request_failed["method"] == "GET"
    assert request_failed["url"] == (
        "https://centric.example.com/api/v2/count/Style?foo=bar&decoded=true"
    )


def test_delta_zero_count_skips_empty_raw_file(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

        def request(self, *_args, **_kwargs):
            return _JsonResponse({"count": 0})

    spec = EndpointSpec(
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

    result = run_endpoint(
        spec,
        Auth(),
        fetcher_cfg,
        append_output=True,
        output_file_suffix=".delta",
        create_empty_output=False,
    )

    assert result.items_fetched == 0
    assert result.expected_count == 0
    assert not result.output_file_created
    assert not result.output_file.exists()
    checkpoint = json.loads(result.checkpoint_file.read_text(encoding="utf-8"))
    assert checkpoint["completed"] is True
    assert "output_file" not in checkpoint


def test_full_zero_count_keeps_empty_raw_file(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

        def request(self, *_args, **_kwargs):
            return _JsonResponse({"count": 0})

    spec = EndpointSpec(
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

    result = run_endpoint(spec, Auth(), fetcher_cfg, create_empty_output=True)

    assert result.items_fetched == 0
    assert result.expected_count == 0
    assert result.output_file_created
    assert result.output_file.is_file()
    assert result.output_file.read_text(encoding="utf-8") == ""
