from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from centric_api.swagger import build_swagger_index


def _swagger_doc(
    paths: list[str] | None = None,
    *,
    style_create_properties: list[str] | None = None,
) -> dict[str, Any]:
    selected_paths = paths or ["/v2/color_specifications", "/v2/styles"]
    style_create_properties = style_create_properties or ["code", "node_name"]
    payload: dict[str, Any] = {
        "swagger": "2.0",
        "paths": {},
        "definitions": {
            "StyleCreate": {
                "type": "object",
                "required": ["code"],
                "properties": {
                    key: value
                    for key, value in {
                        "code": {"type": "string", "description": "The style code."},
                        "node_name": {
                            "type": "string",
                            "description": "The display name of the Style.",
                        },
                    }.items()
                    if key in style_create_properties
                },
            }
        },
    }
    for path in selected_paths:
        endpoint = path.rsplit("/", 1)[-1]
        info_schema = f"{endpoint} information"
        payload["definitions"][info_schema] = {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "The unique identifier."},
                "node_name": {"type": "string", "description": "The display name."},
            },
        }
        payload["paths"][path] = {
            "get": {
                "operationId": f"list{endpoint.title().replace('_', '')}",
                "parameters": [{"name": "decoded", "in": "query"}],
                "responses": {
                    "200": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": f"#/definitions/{info_schema}"},
                        }
                    }
                },
            }
        }
    if "/v2/styles" in selected_paths:
        payload["paths"]["/v2/styles"]["post"] = {
            "operationId": "createStyle",
            "parameters": [
                {
                    "name": "body",
                    "in": "body",
                    "schema": {"$ref": "#/definitions/StyleCreate"},
                }
            ],
            "responses": {"201": {"schema": {"$ref": "#/definitions/styles information"}}},
        }
    return payload


def _write_home_swagger(tmp_path: Path, monkeypatch, document: dict[str, Any]) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    swagger_path = home / "swagger" / "current.json"
    swagger_path.parent.mkdir(parents=True)
    swagger_path.write_text(json.dumps(document), encoding="utf-8")
    return swagger_path


def _write_history_snapshot(
    home: Path,
    snapshot_id: str,
    document: dict[str, Any],
    *,
    fetched_at: str,
) -> None:
    history_dir = home / "swagger" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{snapshot_id}.json"
    meta_path = history_dir / f"{snapshot_id}.meta.json"
    index = build_swagger_index(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "fetched_at": fetched_at,
                "path": str(path),
                "swagger_version": index.swagger_version,
                "operation_count": index.operation_count,
                "endpoint_count": len(index.endpoints),
                "field_schema_count": len(
                    {
                        schema
                        for operation in index.operations
                        for schema in (operation.request_schema, operation.response_schema)
                        if schema
                    }
                ),
                "field_count": sum(
                    len(operation.request_fields) + len(operation.response_fields)
                    for operation in index.operations
                ),
                "sha256": f"sha-{snapshot_id}",
            }
        ),
        encoding="utf-8",
    )


def _fake_auth_context(document: dict[str, Any]):
    class Response:
        status_code = 200
        text = json.dumps(document)

        def json(self) -> dict[str, Any]:
            return document

    class FakeAuthContext:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeAuthContext:
            return self

        def __exit__(self, *_args: Any) -> None:
            pass

        def request(self, method: str, url: str):
            assert method == "GET"
            assert url.endswith("/api/v2/swagger.json")
            return Response()

    return FakeAuthContext
