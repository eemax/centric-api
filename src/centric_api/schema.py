from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import (
    ConfigError,
    default_config_exists,
    read_config_text,
    resolve_optional_private_config_path,
)

DEFAULT_ENDPOINT_SCHEMA_PATH = Path("config/endpoint-schema.yml")
PRIVATE_ENDPOINT_SCHEMA_PATH = Path("endpoint-schema.yml")
ROOT_CONFIG_KEYS = {"version", "endpoints"}
ENDPOINT_CONFIG_KEYS = {"delete_when_any", "delete_when_any_add"}
DELETE_CONDITION_KEYS = {"field", "equals"}


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
        "bom_masters",
        "boms",
        "bom_sections",
        "bom_lines",
        "supplier_quote_masters",
        "supplier_quotes",
        "suppliers",
        "factories",
        "color_specifications",
        "compositions",
        "material_compositions",
        "countries",
        "shipping_ports",
        "lookup_items",
        "lookup_item_subtypes",
        "material_types",
        "style_types",
        "product_sources",
        "documents",
        "document_revisions",
        "duty_rates",
        "users",
    )
}


def load_endpoint_schemas(path: Path | None = None) -> dict[str, EndpointSchema]:
    schemas = dict(DEFAULT_ENDPOINT_SCHEMAS)
    if default_config_exists(DEFAULT_ENDPOINT_SCHEMA_PATH):
        schemas = _apply_endpoint_schema_file(schemas, DEFAULT_ENDPOINT_SCHEMA_PATH)

    overlay_path = (
        Path(path)
        if path is not None
        else resolve_optional_private_config_path(PRIVATE_ENDPOINT_SCHEMA_PATH)
    )
    if overlay_path is None:
        return schemas
    if not overlay_path.is_file():
        raise ConfigError(f"Endpoint schema file not found: {overlay_path}")
    return _apply_endpoint_schema_file(schemas, overlay_path)


def _apply_endpoint_schema_file(
    schemas: dict[str, EndpointSchema],
    resolved_path: Path,
) -> dict[str, EndpointSchema]:
    text = read_config_text(
        resolved_path,
        missing_message="Endpoint schema file not found: {path}",
    )
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Endpoint schema root must be an object: {resolved_path}")
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, f"Endpoint schema {resolved_path}")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError(f"Endpoint schema version must be 1: {resolved_path}")

    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, dict):
        raise ConfigError(f"Endpoint schema 'endpoints' must be an object: {resolved_path}")

    merged = dict(schemas)
    for endpoint_name, config in endpoints.items():
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ConfigError(
                f"Endpoint schema for {endpoint_name!r} must be an object: {resolved_path}"
            )
        _reject_unknown_keys(
            config,
            ENDPOINT_CONFIG_KEYS,
            f"Endpoint schema {resolved_path} endpoint[{endpoint_name}]",
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
        raise ConfigError("Endpoint schema delete_when_any must be an array of objects.")

    conditions: list[DeleteCondition] = []
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError("Endpoint schema delete_when_any entries must be objects.")
        _reject_unknown_keys(item, DELETE_CONDITION_KEYS, "Endpoint schema delete_when_any entry")
        field = item.get("field")
        if not isinstance(field, str) or not field.strip():
            raise ConfigError("Endpoint schema delete_when_any entries require a field.")
        if "equals" not in item:
            raise ConfigError("Endpoint schema delete_when_any entries require equals.")
        conditions.append(DeleteCondition(field=field, equals=item["equals"]))
    return tuple(conditions)


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{field_name} has unknown keys: {', '.join(unknown)}.")
