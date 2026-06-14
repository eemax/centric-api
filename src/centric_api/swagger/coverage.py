from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import load_fetcher_settings
from .index import SwaggerIndex


def coverage_report(index: SwaggerIndex, fetch_config_path: str | Path) -> dict[str, Any]:
    _fetcher_cfg, _auth_settings, endpoint_specs = load_fetcher_settings(fetch_config_path)
    configured_by_endpoint = {
        _endpoint_from_path(_configured_operation_path(endpoint.api_version, endpoint.path)): {
            "name": endpoint.name,
            "path": _configured_operation_path(endpoint.api_version, endpoint.path),
        }
        for endpoint in endpoint_specs
    }
    swagger_by_endpoint = {
        operation.endpoint: operation.path
        for operation in index.operations
        if operation.method == "GET" and _is_collection_path(operation.path)
    }
    configured_endpoints = set(configured_by_endpoint)
    swagger_endpoints = set(swagger_by_endpoint)
    missing_in_swagger = sorted(configured_endpoints - swagger_endpoints)
    missing_in_config = sorted(swagger_endpoints - configured_endpoints)
    return {
        "configured_count": len(configured_endpoints),
        "swagger_get_collection_count": len(swagger_endpoints),
        "missing_in_swagger": [
            configured_by_endpoint[endpoint] for endpoint in missing_in_swagger
        ],
        "missing_in_config": [
            {"path": swagger_by_endpoint[endpoint], "endpoint": endpoint}
            for endpoint in missing_in_config
        ],
        "missing_in_swagger_count": len(missing_in_swagger),
        "missing_in_config_count": len(missing_in_config),
    }


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
