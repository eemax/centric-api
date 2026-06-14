from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import load_fetcher_settings
from .index import SwaggerIndex, SwaggerOperation


def coverage_report(index: SwaggerIndex, fetch_config_path: str | Path) -> dict[str, Any]:
    _fetcher_cfg, _auth_settings, endpoint_specs = load_fetcher_settings(fetch_config_path)
    configured_by_endpoint = {
        _endpoint_from_path(_configured_operation_path(endpoint.api_version, endpoint.path)): {
            "name": endpoint.name,
            "configured_path": _configured_operation_path(endpoint.api_version, endpoint.path),
        }
        for endpoint in endpoint_specs
    }
    swagger_by_endpoint = _swagger_get_collections(index)
    configured_endpoints = set(configured_by_endpoint)
    swagger_endpoints = set(swagger_by_endpoint)
    covered_endpoints = sorted(configured_endpoints & swagger_endpoints)
    missing_in_swagger = sorted(configured_endpoints - swagger_endpoints)
    missing_in_config = sorted(swagger_endpoints - configured_endpoints)
    missing_in_config_rows = sorted(
        (_swagger_record(index, swagger_by_endpoint[endpoint]) for endpoint in missing_in_config),
        key=_interesting_swagger_row_key,
    )
    return {
        "configured_count": len(configured_endpoints),
        "swagger_get_collection_count": len(swagger_endpoints),
        "covered_count": len(covered_endpoints),
        "swagger_only_post_count": sum(1 for row in missing_in_config_rows if row["has_post"]),
        "covered": [
            {
                **configured_by_endpoint[endpoint],
                **_swagger_record(index, swagger_by_endpoint[endpoint]),
            }
            for endpoint in covered_endpoints
        ],
        "missing_in_swagger": [configured_by_endpoint[endpoint] for endpoint in missing_in_swagger],
        "missing_in_config": missing_in_config_rows,
        "missing_in_swagger_count": len(missing_in_swagger),
        "missing_in_config_count": len(missing_in_config),
    }


def _swagger_get_collections(index: SwaggerIndex) -> dict[str, str]:
    return {
        operation.endpoint: operation.path
        for operation in index.operations
        if operation.method == "GET" and _is_collection_path(operation.path)
    }


def _swagger_record(index: SwaggerIndex, path: str) -> dict[str, Any]:
    get_operation = _operation(index, path, "GET")
    post_operation = _operation(index, path, "POST")
    response_fields = get_operation.response_fields if get_operation else ()
    post_fields = post_operation.request_fields if post_operation else ()
    return {
        "endpoint": _endpoint_from_path(path),
        "swagger_path": path,
        "response_schema": get_operation.response_schema if get_operation else None,
        "response_field_count": len(response_fields),
        "required_response_field_count": sum(1 for field in response_fields if field.required),
        "has_post": post_operation is not None,
        "post_schema": post_operation.request_schema if post_operation else None,
        "post_field_count": len(post_fields),
        "required_post_field_count": sum(1 for field in post_fields if field.required),
    }


def _operation(index: SwaggerIndex, path: str, method: str) -> SwaggerOperation | None:
    return next(
        (
            operation
            for operation in index.operations
            if operation.path == path and operation.method == method
        ),
        None,
    )


def _interesting_swagger_row_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        0 if row["has_post"] else 1,
        -int(row["response_field_count"]),
        -int(row["post_field_count"]),
        str(row["endpoint"]),
    )


def _configured_operation_path(api_version: str, path: str) -> str:
    return f"/{api_version.strip().strip('/')}/{path.strip().strip('/')}"


def _is_collection_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[0] in {"v1", "v2", "v3"}:
        parts = parts[1:]
    return len(parts) == 1 and not any(part.startswith("{") for part in parts)


def _endpoint_from_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[0] in {"v1", "v2", "v3"}:
        parts = parts[1:]
    return parts[0] if parts else ""
