from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from .client import CentricClient, FetchError
from .config import EndpointSpec
from .db import iso, transaction, utc_now

OBJECT_TYPE_RE = re.compile(r"Object Type:\s*([A-Za-z0-9_]+)")
# HAL navigation metadata, not record data.
EXCLUDED_FIELDS = frozenset({"_links"})
SCALAR_TYPES = {"string", "integer", "number", "boolean"}


@dataclass(frozen=True)
class FieldPlan:
    field: str
    kind: str  # "column", "list" or "map"
    value_type: str  # "text", "integer", "real", "boolean", "datetime" or "json"
    spec_type: str
    object_type: str | None


@dataclass(frozen=True)
class SwaggerRefresh:
    api_version: str
    status: str  # "unchanged", "updated", "failed"
    message: str | None = None


def refresh_specs(
    conn: sqlite3.Connection, client: CentricClient, api_versions: set[str]
) -> list[SwaggerRefresh]:
    """Download the spec for each API version and store it if its content changed."""
    results: list[SwaggerRefresh] = []
    for api_version in sorted(api_versions):
        url = client.url(api_version, "swagger.json")
        try:
            spec = client.get_json(url)
        except FetchError as exc:
            results.append(SwaggerRefresh(api_version, "failed", str(exc)))
            continue
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            results.append(
                SwaggerRefresh(api_version, "failed", f"{url} did not return a swagger document.")
            )
            continue
        # Keep the spec's own key order (it sets silver column order); hash a sorted copy.
        text = json.dumps(spec, separators=(",", ":"), ensure_ascii=False)
        canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with transaction(conn):
            row = conn.execute(
                "SELECT sha256 FROM meta_swagger WHERE api_version = ?", [api_version]
            ).fetchone()
            if row is not None and row["sha256"] == sha256:
                conn.execute(
                    "UPDATE meta_swagger SET fetched_at = ? WHERE api_version = ?",
                    [iso(utc_now()), api_version],
                )
                results.append(SwaggerRefresh(api_version, "unchanged"))
                continue
            conn.execute(
                """
                INSERT INTO meta_swagger (api_version, sha256, fetched_at, spec)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (api_version) DO UPDATE SET
                    sha256 = excluded.sha256, fetched_at = excluded.fetched_at,
                    spec = excluded.spec
                """,
                [api_version, sha256, iso(utc_now()), text],
            )
        results.append(SwaggerRefresh(api_version, "updated"))
    return results


def load_specs(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["api_version"]: json.loads(row["spec"])
        for row in conn.execute("SELECT api_version, spec FROM meta_swagger")
    }


def endpoint_plan(
    specs: dict[str, dict[str, Any]], endpoint: EndpointSpec
) -> tuple[list[FieldPlan] | None, str | None]:
    """Return the field plan for an endpoint and a note about how it was resolved.

    Uses the spec for the endpoint's API version, falling back to v2 when that version's spec
    is not available. Returns (None, reason) when no schema can be found.
    """
    note = None
    spec = specs.get(endpoint.api_version)
    if spec is None and endpoint.api_version != "v2" and "v2" in specs:
        spec = specs["v2"]
        note = f"no {endpoint.api_version} spec stored; using the v2 spec"
    if spec is None:
        return None, f"no {endpoint.api_version} spec stored"
    schema = _response_item_schema(spec, endpoint.path)
    if schema is None:
        return None, f"no GET /{endpoint.path} response schema in the spec"
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None, f"GET /{endpoint.path} response schema has no properties"
    plan = [
        _field_plan(name, definition)
        for name, definition in properties.items()
        if name not in EXCLUDED_FIELDS and isinstance(definition, dict)
    ]
    return plan, note


def _response_item_schema(spec: dict[str, Any], path: str) -> dict[str, Any] | None:
    operation = spec.get("paths", {}).get(f"/{path}", {}).get("get")
    if not isinstance(operation, dict):
        return None
    response = operation.get("responses", {}).get("200")
    if not isinstance(response, dict):
        return None
    schema = response.get("schema")
    if schema is None:  # OpenAPI 3
        content = response.get("content", {})
        media = content.get("application/json") or next(iter(content.values()), None)
        schema = media.get("schema") if isinstance(media, dict) else None
    schema = _resolve(spec, schema)
    if isinstance(schema, dict) and schema.get("type") == "array":
        schema = _resolve(spec, schema.get("items"))
    return schema if isinstance(schema, dict) else None


def _resolve(spec: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    if not isinstance(schema, dict) or "$ref" not in schema or depth > 10:
        return schema
    ref = schema["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return _resolve(spec, node, depth + 1)


def _field_plan(name: str, definition: dict[str, Any]) -> FieldPlan:
    description = definition.get("description")
    match = OBJECT_TYPE_RE.search(description) if isinstance(description, str) else None
    object_type = match.group(1) if match else None
    spec_type = _spec_type(definition)
    kind_type = definition.get("type")
    if kind_type == "array":
        items = definition.get("items")
        value_type = _scalar_value_type(items) if isinstance(items, dict) else None
        return FieldPlan(name, "list", value_type or "json", spec_type, object_type)
    if kind_type == "object":
        values = definition.get("additionalProperties")
        value_type = _scalar_value_type(values) if isinstance(values, dict) else None
        if value_type is not None:
            return FieldPlan(name, "map", value_type, spec_type, object_type)
        return FieldPlan(name, "column", "json", spec_type, object_type)
    return FieldPlan(
        name, "column", _scalar_value_type(definition) or "json", spec_type, object_type
    )


def _scalar_value_type(definition: dict[str, Any]) -> str | None:
    kind = definition.get("type")
    if kind not in SCALAR_TYPES or "$ref" in definition:
        return None
    if kind == "string":
        return "datetime" if definition.get("format") == "date-time" else "text"
    return {"integer": "integer", "number": "real", "boolean": "boolean"}[kind]


def _spec_type(definition: dict[str, Any]) -> str:
    if "$ref" in definition:
        return "ref"
    kind = str(definition.get("type", "unknown"))
    if kind == "array" and isinstance(definition.get("items"), dict):
        return f"array<{_spec_type(definition['items'])}>"
    if kind == "object" and isinstance(definition.get("additionalProperties"), dict):
        return f"map<{_spec_type(definition['additionalProperties'])}>"
    fmt = definition.get("format")
    return f"{kind}:{fmt}" if fmt else kind
