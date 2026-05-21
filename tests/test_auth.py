from __future__ import annotations

import json
from pathlib import Path

import httpx

from centric_api.auth import AuthContext


def test_auth_context_reuses_cached_token(tmp_path: Path) -> None:
    token_path = tmp_path / "auth" / "token.json"
    token_path.parent.mkdir()
    token_path.write_text(
        json.dumps(
            {
                "base_url": "https://centric.example.com",
                "username": "user",
                "token": "cached-token",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    client = _Client(post_token="fresh-token")
    auth = AuthContext(
        base_url="https://centric.example.com",
        username="user",
        password=None,
        timeout=30,
        client=client,
        token_cache_path=token_path,
    )

    assert auth.ensure_token() == "cached-token"
    assert client.post_count == 0


def test_auth_context_writes_token_cache_with_private_permissions(tmp_path: Path) -> None:
    token_path = tmp_path / "auth" / "token.json"
    client = _Client(post_token="fresh-token")
    auth = AuthContext(
        base_url="https://centric.example.com",
        username="user",
        password="pass",
        timeout=30,
        client=client,
        token_cache_path=token_path,
    )

    assert auth.ensure_token() == "fresh-token"

    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert payload["base_url"] == "https://centric.example.com"
    assert payload["username"] == "user"
    assert payload["token"] == "fresh-token"
    assert token_path.stat().st_mode & 0o777 == 0o600


def test_auth_context_refreshes_cached_token_on_401(tmp_path: Path) -> None:
    token_path = tmp_path / "auth" / "token.json"
    token_path.parent.mkdir()
    token_path.write_text(
        json.dumps(
            {
                "base_url": "https://centric.example.com",
                "username": "user",
                "token": "stale-token",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    client = _Client(post_token="fresh-token", request_statuses=[401, 200])
    auth = AuthContext(
        base_url="https://centric.example.com",
        username="user",
        password="pass",
        timeout=30,
        client=client,
        token_cache_path=token_path,
    )

    response = auth.request("GET", "https://centric.example.com/api/v2/styles")

    assert response.status_code == 200
    assert client.request_auth_headers == ["Bearer stale-token", "Bearer fresh-token"]
    assert json.loads(token_path.read_text(encoding="utf-8"))["token"] == "fresh-token"


class _Client:
    def __init__(self, *, post_token: str, request_statuses: list[int] | None = None) -> None:
        self.post_token = post_token
        self.request_statuses = request_statuses or [200]
        self.post_count = 0
        self.request_count = 0
        self.request_auth_headers: list[str] = []

    def post(self, *_args, **_kwargs) -> httpx.Response:
        self.post_count += 1
        return httpx.Response(200, json={"token": self.post_token})

    def request(self, *_args, **kwargs) -> httpx.Response:
        self.request_auth_headers.append(kwargs["headers"]["Authorization"])
        status_code = self.request_statuses[min(self.request_count, len(self.request_statuses) - 1)]
        self.request_count += 1
        return httpx.Response(status_code, json={})

    def close(self) -> None:
        return None
