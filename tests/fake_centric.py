from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from centric_api.client import CentricClient, Credentials
from centric_api.config import EndpointSpec, HttpSettings, Settings

BASE_URL = "https://acme.centricsoftware.com/csi-requesthandler"

STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "_modified_at": {"type": "string"},
        "_links": {"type": "object", "additionalProperties": {"type": "string"}},
        "node_name": {"type": "string"},
        "active": {"type": "boolean"},
        "net_weight": {"type": "number", "format": "double"},
        "sort_order": {"type": "integer", "format": "int32"},
        "launched_at": {"type": "string", "format": "date-time"},
        "product_colors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Colors. Object Type: Colorway",
        },
        "product_sources": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Sources. Object Type: ProductSource",
        },
    },
}
COLORWAY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "_modified_at": {"type": "string"},
        "node_name": {"type": "string"},
        "style": {"type": "string", "description": "Object Type: Style"},
    },
}


def swagger(schemas: dict[str, dict[str, Any]], paths: dict[str, str]) -> dict[str, Any]:
    return {
        "swagger": "2.0",
        "definitions": schemas,
        "paths": {
            f"/{path}": {
                "get": {
                    "responses": {
                        "200": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": f"#/definitions/{schema}"},
                            }
                        }
                    }
                }
            }
            for path, schema in paths.items()
        },
    }


def default_specs() -> dict[str, dict[str, Any]]:
    return {
        "v2": swagger(
            {"Style information": STYLE_SCHEMA, "Colorway information": COLORWAY_SCHEMA},
            {"styles": "Style information", "colorways": "Colorway information"},
        )
    }


def ts(minute: int) -> str:
    return datetime(2026, 1, 1, 0, minute, tzinfo=UTC).isoformat().replace("+00:00", "Z")


class FakeCentric:
    """In-memory Centric API: session login, swagger, counts and sorted, filtered paging."""

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {"styles": [], "colorways": []}
        self.paths = {"styles": "styles", "colorways": "colorways"}
        self.count_paths = {"count/Style": "styles", "count/Colorway": "colorways"}
        self.specs = default_specs()
        self.logins = 0
        self.requests: list[httpx.Request] = []
        self.fail_page_after: int | None = None
        self.page_hook: Callable[[str, int], None] | None = None
        self.count_offset = 0
        self.expire_token_once = False
        self.status_queue: list[int] = []
        self.retry_after: str | None = None
        self.login_status_queue: list[int] = []
        self.ignore_id_filter = False
        self.page_cap: int | None = None
        self.ignore_skip = False
        self.lookups = 0
        self._pages_served = 0

    def add(self, endpoint: str, **record: Any) -> dict[str, Any]:
        self.records[endpoint] = [r for r in self.records[endpoint] if r["id"] != record["id"]]
        self.records[endpoint].append(record)
        return record

    def remove(self, endpoint: str, record_id: str) -> None:
        self.records[endpoint] = [r for r in self.records[endpoint] if r["id"] != record_id]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self, http: HttpSettings | None = None) -> CentricClient:
        return CentricClient(
            Credentials(BASE_URL, "user", "secret"),
            http or HttpSettings(retry_base_seconds=0.01, retry_max_seconds=0.01),
            transport=self.transport(),
            sleep=lambda _seconds: None,
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/csi-requesthandler/api/")
        if request.method == "POST" and path == "v2/session":
            if self.login_status_queue:
                return httpx.Response(self.login_status_queue.pop(0), text="login busy")
            self.logins += 1
            return httpx.Response(200, json={"token": f"SecurityTokenURL=token{self.logins}"})
        if request.headers.get("Authorization") != f"Bearer token{self.logins}":
            return httpx.Response(401, json={"message": "unauthorized"})
        if self.expire_token_once:
            self.expire_token_once = False
            return httpx.Response(401, json={"message": "expired"})
        if self.status_queue:
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(self.status_queue.pop(0), text="busy", headers=headers)
        version, _, rest = path.partition("/")
        if rest == "swagger.json":
            spec = self.specs.get(version)
            return httpx.Response(200, json=spec) if spec else httpx.Response(404, text="nope")
        params = request.url.params
        if rest in self.count_paths:
            rows = self._filtered(self.count_paths[rest], params)
            return httpx.Response(200, json={"count": len(rows) + self.count_offset})
        endpoint = next((name for name, p in self.paths.items() if p == rest), None)
        if endpoint is None:
            return httpx.Response(404, text="unknown path")
        assert params.get("decoded") == "true"
        if "id" in params and not self.ignore_id_filter:
            self.lookups += 1
            wanted = set(params.get_list("id"))
            assert len(wanted) <= int(params["limit"])
            rows = [r for r in self.records[endpoint] if r["id"] in wanted]
            return httpx.Response(200, json=copy.deepcopy(rows))
        if "id" not in params:
            assert params.get("sort") == "_modified_at"
        skip, limit = int(params.get("skip", 0)), int(params["limit"])
        if self.fail_page_after is not None and self._pages_served >= self.fail_page_after:
            return httpx.Response(400, json={"message": "boom"})
        self._pages_served += 1
        if self.page_hook is not None:
            self.page_hook(endpoint, skip)
        if "id" in params:  # ignore_id_filter: behave like an endpoint without id filtering
            self.lookups += 1
            skip, limit = 0, int(params["limit"])
        rows = sorted(self._filtered(endpoint, params), key=lambda r: (r["_modified_at"], r["id"]))
        if self.page_cap is not None:
            limit = min(limit, self.page_cap)
        if self.ignore_skip:
            skip = 0
        return httpx.Response(200, json=copy.deepcopy(rows[skip : skip + limit]))

    def _filtered(self, endpoint: str, params: httpx.QueryParams) -> list[dict[str, Any]]:
        rows = self.records[endpoint]
        floor = params.get("_modified_at")
        if floor:
            assert floor.startswith("ge")
            floor_dt = _parse(floor[2:])
            rows = [r for r in rows if _parse(r["_modified_at"]) >= floor_dt]
        return rows


def make_settings(tmp_path: Path, *, limit: int = 2, overlap_minutes: int = 0) -> Settings:
    return Settings(
        database=tmp_path / "centric.db",
        env_file=None,
        http=HttpSettings(retry_base_seconds=0.01, retry_max_seconds=0.01),
        overlap_minutes=overlap_minutes,
        endpoints=(
            EndpointSpec("styles", "styles", "v2", "count/Style", limit=limit),
            EndpointSpec("colorways", "colorways", "v2", "count/Colorway", limit=limit),
        ),
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
