from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass(frozen=True)
class SwaggerOperation:
    path: str
    method: str
    endpoint: str
    operation_id: str | None
    summary: str | None
    tags: tuple[str, ...]
    parameter_names: tuple[str, ...]
    body_fields: tuple[str, ...]
    required_body_fields: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class SwaggerIndex:
    swagger_version: str | None
    operation_count: int
    endpoints: tuple[str, ...]
    operations: tuple[SwaggerOperation, ...]

    @property
    def operation_keys(self) -> set[tuple[str, str]]:
        return {(operation.path, operation.method) for operation in self.operations}


def build_swagger_index(document: dict[str, Any]) -> SwaggerIndex:
    paths = document.get("paths")
    operations: list[SwaggerOperation] = []
    if isinstance(paths, dict):
        for raw_path, path_item in sorted(paths.items()):
            if not isinstance(raw_path, str) or not isinstance(path_item, dict):
                continue
            path = _normalize_path(raw_path)
            for raw_method, operation in sorted(path_item.items()):
                method = str(raw_method).lower()
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operations.append(_operation(document, path, method, operation))
    endpoints = tuple(
        sorted({operation.endpoint for operation in operations if operation.endpoint})
    )
    version = document.get("swagger") or document.get("openapi")
    return SwaggerIndex(
        swagger_version=str(version) if version else None,
        operation_count=len(operations),
        endpoints=endpoints,
        operations=tuple(operations),
    )


def _operation(
    document: dict[str, Any],
    path: str,
    method: str,
    operation: dict[str, Any],
) -> SwaggerOperation:
    parameters = operation.get("parameters")
    parameter_names: list[str] = []
    body_fields: tuple[str, ...] = ()
    required_body_fields: tuple[str, ...] = ()
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name")
            if isinstance(name, str):
                parameter_names.append(name)
            if parameter.get("in") == "body":
                schema = _resolve_schema(document, parameter.get("schema"))
                body_fields = _schema_property_names(schema)
                required_body_fields = _schema_required_names(schema)

    tags = operation.get("tags")
    return SwaggerOperation(
        path=path,
        method=method.upper(),
        endpoint=_endpoint_from_path(path),
        operation_id=_string_or_none(operation.get("operationId")),
        summary=_string_or_none(operation.get("summary")),
        tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else (),
        parameter_names=tuple(sorted(set(parameter_names))),
        body_fields=body_fields,
        required_body_fields=required_body_fields,
        fingerprint=_fingerprint(operation),
    )


def _normalize_path(path: str) -> str:
    text = "/" + path.strip().strip("/")
    if text.startswith("/api/"):
        text = text[len("/api") :]
    return text


def _endpoint_from_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part and not part.startswith("{")]
    if parts and parts[0] in {"v1", "v2", "v3"}:
        parts = parts[1:]
    return parts[0] if parts else ""


def _resolve_schema(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    current: Any = document
    for part in ref[2:].split("/"):
        if not isinstance(current, dict) or part not in current:
            return {}
        current = current[part]
    return current if isinstance(current, dict) else {}


def _schema_property_names(schema: dict[str, Any]) -> tuple[str, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(sorted(str(name) for name in properties))


def _schema_required_names(schema: dict[str, Any]) -> tuple[str, ...]:
    required = schema.get("required")
    if not isinstance(required, list):
        return ()
    return tuple(sorted(str(name) for name in required))


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
