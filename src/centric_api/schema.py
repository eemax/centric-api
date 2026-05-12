from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import resolve_optional_private_config_path

DEFAULT_ENDPOINT_SCHEMA_PATH = Path("config/endpoint-schema.yml")
PRIVATE_ENDPOINT_SCHEMA_PATH = Path("endpoint-schema.yml")


@dataclass(frozen=True)
class DeleteCondition:
    field: str
    equals: Any


@dataclass(frozen=True)
class EndpointSchema:
    name: str
    delete_when_any: tuple[DeleteCondition, ...] = ()


DEFAULT_ENDPOINT_SCHEMAS: dict[str, EndpointSchema] = {
    name: EndpointSchema(name=name)
    for name in (
        "styles",
        "colorways",
        "collections",
        "category1s",
        "category2s",
        "sizes",
        "seasons",
        "materials",
        "boms",
        "bom_section_definitions",
        "bomrows",
        "supplierquotes",
        "suppliers",
        "factories",
    )
}


def load_endpoint_schemas(path: Path | None = None) -> dict[str, EndpointSchema]:
    schemas = dict(DEFAULT_ENDPOINT_SCHEMAS)
    if DEFAULT_ENDPOINT_SCHEMA_PATH.is_file():
        schemas = _apply_endpoint_schema_file(schemas, DEFAULT_ENDPOINT_SCHEMA_PATH)

    overlay_path = (
        Path(path)
        if path is not None
        else resolve_optional_private_config_path(PRIVATE_ENDPOINT_SCHEMA_PATH)
    )
    if overlay_path is None:
        return schemas
    if not overlay_path.is_file():
        raise ValueError(f"Endpoint schema file not found: {overlay_path}")
    return _apply_endpoint_schema_file(schemas, overlay_path)


def _apply_endpoint_schema_file(
    schemas: dict[str, EndpointSchema],
    resolved_path: Path,
) -> dict[str, EndpointSchema]:
    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Endpoint schema root must be an object: {resolved_path}")

    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, dict):
        raise ValueError(f"Endpoint schema 'endpoints' must be an object: {resolved_path}")

    merged = dict(schemas)
    for endpoint_name, config in endpoints.items():
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError(
                f"Endpoint schema for {endpoint_name!r} must be an object: {resolved_path}"
            )
        name = str(endpoint_name)
        default = merged.get(name, EndpointSchema(name=name))
        delete_when_any = _merged_delete_conditions(config, default)
        merged[name] = EndpointSchema(
            name=name,
            delete_when_any=delete_when_any,
        )
    return merged


def _merged_delete_conditions(
    config: dict[str, Any],
    default: EndpointSchema,
) -> tuple[DeleteCondition, ...]:
    conditions = (
        _delete_condition_tuple(config["delete_when_any"])
        if "delete_when_any" in config
        else default.delete_when_any
    )
    additions = _delete_condition_tuple(config.get("delete_when_any_add"))
    return conditions + additions


def _delete_condition_tuple(value: Any) -> tuple[DeleteCondition, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("Endpoint schema delete_when_any must be an array of objects.")

    conditions: list[DeleteCondition] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Endpoint schema delete_when_any entries must be objects.")
        field = item.get("field")
        if not isinstance(field, str) or not field.strip():
            raise ValueError("Endpoint schema delete_when_any entries require a field.")
        if "equals" not in item:
            raise ValueError("Endpoint schema delete_when_any entries require equals.")
        conditions.append(DeleteCondition(field=field, equals=item["equals"]))
    return tuple(conditions)
