from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass(frozen=True)
class SwaggerField:
    name: str
    type: str | None
    item_type: str | None
    ref: str | None
    object_type: str | None
    format: str | None
    enum: tuple[Any, ...] | None
    description: str | None
    required: bool
    fingerprint: str


@dataclass(frozen=True)
class SwaggerOperation:
    path: str
    method: str
    endpoint: str
    operation_id: str | None
    summary: str | None
    tags: tuple[str, ...]
    parameter_names: tuple[str, ...]
    request_schema: str | None
    response_schema: str | None
    request_fields: tuple[SwaggerField, ...]
    response_fields: tuple[SwaggerField, ...]
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
    request_schema: str | None = None
    request_fields: tuple[SwaggerField, ...] = ()
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
                request_schema = _schema_name(parameter.get("schema"))
                schema = _resolve_schema(document, parameter.get("schema"))
                request_fields = _schema_fields(schema)
                body_fields = _schema_property_names(schema)
                required_body_fields = _schema_required_names(schema)

    response_schema, response_fields = _response_schema_fields(document, operation)
    tags = operation.get("tags")
    return SwaggerOperation(
        path=path,
        method=method.upper(),
        endpoint=_endpoint_from_path(path),
        operation_id=_string_or_none(operation.get("operationId")),
        summary=_string_or_none(operation.get("summary")),
        tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else (),
        parameter_names=tuple(sorted(set(parameter_names))),
        request_schema=request_schema,
        response_schema=response_schema,
        request_fields=request_fields,
        response_fields=response_fields,
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


def _response_schema_fields(
    document: dict[str, Any],
    operation: dict[str, Any],
) -> tuple[str | None, tuple[SwaggerField, ...]]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None, ()
    for status_code in ("200", "201", "default"):
        response = responses.get(status_code)
        if not isinstance(response, dict):
            continue
        if "schema" not in response:
            continue
        schema_ref = response.get("schema")
        schema_name = _schema_name(schema_ref)
        schema = _resolve_response_schema(document, schema_ref)
        return schema_name, _schema_fields(schema)
    return None, ()


def _resolve_response_schema(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    if schema.get("type") == "array":
        items = schema.get("items")
        return _resolve_schema(document, items)
    return _resolve_schema(document, schema)


def _schema_name(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    if schema.get("type") == "array":
        return _schema_name(schema.get("items"))
    return None


def _schema_fields(schema: dict[str, Any]) -> tuple[SwaggerField, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    required_names = set(_schema_required_names(schema))
    fields = [
        _field(name, spec, required=str(name) in required_names)
        for name, spec in properties.items()
        if isinstance(name, str) and isinstance(spec, dict)
    ]
    return tuple(sorted(fields, key=lambda field: field.name))


def _field(name: str, spec: dict[str, Any], *, required: bool) -> SwaggerField:
    description = _string_or_none(spec.get("description"))
    item_spec = spec.get("items")
    item_type = _string_or_none(item_spec.get("type")) if isinstance(item_spec, dict) else None
    ref = _string_or_none(spec.get("$ref"))
    if ref is None and isinstance(item_spec, dict):
        ref = _string_or_none(item_spec.get("$ref"))
    return SwaggerField(
        name=name,
        type=_string_or_none(spec.get("type")),
        item_type=item_type,
        ref=ref,
        object_type=_object_type(description),
        format=_string_or_none(spec.get("format")),
        enum=_enum_values(spec.get("enum")),
        description=description,
        required=required,
        fingerprint=_fingerprint(_field_fingerprint_payload(spec, required=required)),
    )


def _field_fingerprint_payload(spec: dict[str, Any], *, required: bool) -> dict[str, Any]:
    return {
        "type": spec.get("type"),
        "items": spec.get("items"),
        "$ref": spec.get("$ref"),
        "description": spec.get("description"),
        "enum": spec.get("enum"),
        "format": spec.get("format"),
        "required": required,
    }


def _object_type(description: str | None) -> str | None:
    if not description or "Object Type:" not in description:
        return None
    return description.split("Object Type:", 1)[1].strip() or None


def _enum_values(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(value)


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
