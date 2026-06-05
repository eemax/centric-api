from __future__ import annotations

import html
import json
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from .config import ConfigError, runtime_path
from .store import connect_readonly, table_exists

ENDPOINT_MAP_DIR = Path("maps/endpoints")
ENDPOINT_MAP_TEMPLATE = "endpoint-map.html"
EMPTY_REF_VALUES = {"", "centric:"}
IDENTITY_PATHS = {"id"}


@dataclass(frozen=True)
class EndpointRelationship:
    source_endpoint: str
    source_path: str
    target_endpoint: str
    array: bool = False


@dataclass(frozen=True)
class EndpointMapResult:
    run_id: str
    artifact_dir: Path
    json_path: Path
    markdown_path: Path
    html_path: Path
    endpoint_count: int
    relationship_count: int
    relationships: tuple[EndpointRelationship, ...]


def build_endpoint_map(
    db_path: Path,
    *,
    output_root: str | Path | None = None,
) -> EndpointMapResult:
    endpoint_names, relationships = _infer_endpoint_relationships_from_db(db_path)
    if not endpoint_names:
        raise ConfigError("Endpoint map requires cached endpoint records. Run fetch first.")
    run_id = _run_id()
    root = (
        Path(output_root).expanduser()
        if output_root is not None
        else runtime_path(ENDPOINT_MAP_DIR)
    )
    artifact_dir = root / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    json_path = artifact_dir / "relationships.json"
    markdown_path = artifact_dir / "endpoint-map.md"
    html_path = artifact_dir / "endpoint-map.html"

    payload = endpoint_map_record(
        run_id=run_id,
        artifact_dir=artifact_dir,
        json_path=json_path,
        markdown_path=markdown_path,
        html_path=html_path,
        endpoint_names=endpoint_names,
        relationships=relationships,
    )
    _write_text(json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _write_text(markdown_path, render_endpoint_map_markdown(payload))
    _write_text(html_path, render_endpoint_map_html(payload))

    return EndpointMapResult(
        run_id=run_id,
        artifact_dir=artifact_dir,
        json_path=json_path,
        markdown_path=markdown_path,
        html_path=html_path,
        endpoint_count=len(endpoint_names),
        relationship_count=len(relationships),
        relationships=relationships,
    )


def infer_endpoint_relationships(
    records: dict[str, tuple[dict[str, Any], ...]],
) -> tuple[EndpointRelationship, ...]:
    id_index = _endpoint_ids(records)
    found: set[EndpointRelationship] = set()
    for source_endpoint, endpoint_records in records.items():
        for record in endpoint_records:
            for path, value, is_array in _iter_scalar_values(record):
                if path in IDENTITY_PATHS:
                    continue
                ref = _clean_ref(value)
                if ref is None:
                    continue
                for target_endpoint in sorted(id_index.get(ref, ())):
                    found.add(
                        EndpointRelationship(
                            source_endpoint=source_endpoint,
                            source_path=path,
                            target_endpoint=target_endpoint,
                            array=is_array,
                        )
                    )
    return tuple(
        sorted(
            found,
            key=lambda item: (item.source_endpoint, item.source_path, item.target_endpoint),
        )
    )


def endpoint_map_record(
    *,
    run_id: str,
    artifact_dir: Path,
    json_path: Path,
    markdown_path: Path,
    html_path: Path,
    endpoint_names: tuple[str, ...],
    relationships: tuple[EndpointRelationship, ...],
) -> dict[str, Any]:
    endpoints = sorted(
        set(endpoint_names)
        | {relationship.source_endpoint for relationship in relationships}
        | {relationship.target_endpoint for relationship in relationships}
    )
    return {
        "run_id": run_id,
        "artifact_dir": str(artifact_dir),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "html_path": str(html_path),
        "endpoint_count": len(endpoint_names),
        "relationship_count": len(relationships),
        "endpoints": endpoints,
        "relationships": [_relationship_record(relationship) for relationship in relationships],
        "by_endpoint": _by_endpoint_records(endpoints, relationships),
    }


def _relationship_record(relationship: EndpointRelationship) -> dict[str, Any]:
    return {
        "source_endpoint": relationship.source_endpoint,
        "source_path": relationship.source_path,
        "target_endpoint": relationship.target_endpoint,
        "target_path": "id",
        "array": relationship.array,
        "join": _join_expression(relationship),
    }


def _by_endpoint_records(
    endpoints: list[str],
    relationships: tuple[EndpointRelationship, ...],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    outgoing: dict[str, list[EndpointRelationship]] = defaultdict(list)
    incoming: dict[str, list[EndpointRelationship]] = defaultdict(list)
    for relationship in relationships:
        outgoing[relationship.source_endpoint].append(relationship)
        incoming[relationship.target_endpoint].append(relationship)
    return {
        endpoint: {
            "outgoing": [
                _relationship_record(relationship)
                for relationship in sorted(
                    outgoing.get(endpoint, []),
                    key=lambda item: (item.source_path, item.target_endpoint),
                )
            ],
            "incoming": [
                _relationship_record(relationship)
                for relationship in sorted(
                    incoming.get(endpoint, []),
                    key=lambda item: (item.source_endpoint, item.source_path),
                )
            ],
        }
        for endpoint in endpoints
    }


def render_endpoint_map_markdown(payload: dict[str, Any]) -> str:
    relationships = payload["relationships"]
    outgoing = _relationships_by(relationships, "source_endpoint")
    incoming = _relationships_by(relationships, "target_endpoint")
    endpoints = sorted(set(payload["endpoints"]) | set(outgoing) | set(incoming))
    lines = [
        "# Endpoint Relationship Map",
        "",
        "Generated from local cached endpoint records.",
        "",
        "## How To Read",
        "",
        "- `source.field -> target` means `source.field` stores IDs from `target.id`.",
        "- `[]` means the reference is inside an array or array-like object.",
        "- Relationships are inferred from cached values matching cached record IDs.",
        "- Field names are not guessed; they are the paths where matching IDs were found.",
        "",
        "## Summary",
        "",
        f"- Endpoints: {payload['endpoint_count']}",
        f"- Relationships: {payload['relationship_count']}",
        "",
        "## High-Level Graph",
        "",
        "| Endpoint | Outgoing Targets | Incoming Sources |",
        "| --- | --- | --- |",
    ]
    for endpoint in endpoints:
        outgoing_targets = _endpoint_list(
            item["target_endpoint"] for item in outgoing.get(endpoint, [])
        )
        incoming_sources = _endpoint_list(
            item["source_endpoint"] for item in incoming.get(endpoint, [])
        )
        lines.append(f"| `{endpoint}` | {outgoing_targets} | {incoming_sources} |")

    lines.extend(
        [
            "",
        "## Endpoints",
        "",
        ]
    )
    for endpoint in endpoints:
        lines.extend([f"### {endpoint}", ""])
        endpoint_outgoing = outgoing.get(endpoint, [])
        lines.append("Outgoing:")
        if endpoint_outgoing:
            lines.extend(
                f"- `{item['source_path']}` -> `{item['target_endpoint']}` "
                f"via `{item['join']}`"
                for item in endpoint_outgoing
            )
        else:
            lines.append("- none detected")
        lines.append("")
        endpoint_incoming = incoming.get(endpoint, [])
        lines.append("Incoming:")
        if endpoint_incoming:
            lines.extend(
                f"- `{item['source_endpoint']}.{item['source_path']}` -> `{endpoint}` "
                f"via `{item['join']}`"
                for item in endpoint_incoming
            )
        else:
            lines.append("- none detected")
        lines.append("")

    lines.extend(["## Join Recipes", ""])
    if relationships:
        lines.extend(f"- Join with `{item['join']}`." for item in relationships)
    else:
        lines.append("- none detected")
    lines.append("")

    lines.extend(
        [
            "## All Relationships",
            "",
            "| Source | Field | Target | Join |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in relationships:
        lines.append(
            f"| `{item['source_endpoint']}` | `{item['source_path']}` | "
            f"`{item['target_endpoint']}` | `{item['join']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def render_endpoint_map_html(payload: dict[str, Any]) -> str:
    template = _endpoint_map_template()
    replacements = {
        "__ENDPOINT_COUNT__": str(payload["endpoint_count"]),
        "__RELATIONSHIP_COUNT__": str(payload["relationship_count"]),
        "__RUN_ID__": html.escape(str(payload["run_id"])),
        "__ENDPOINTS_JSON__": _script_json(payload["endpoints"]),
        "__RELATIONSHIPS_JSON__": _script_json(payload["relationships"], sort_keys=True),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def _endpoint_map_template() -> str:
    return (
        resources.files("centric_api.templates")
        .joinpath(ENDPOINT_MAP_TEMPLATE)
        .read_text(encoding="utf-8")
    )


def _script_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(value, sort_keys=sort_keys).replace("</", "<\\/")


def _join_expression(relationship: EndpointRelationship) -> str:
    return (
        f"{relationship.source_endpoint}.{relationship.source_path} = "
        f"{relationship.target_endpoint}.id"
    )


def _endpoint_list(values) -> str:
    endpoints = sorted(set(values))
    if not endpoints:
        return "none"
    return ", ".join(f"`{endpoint}`" for endpoint in endpoints)


def _infer_endpoint_relationships_from_db(
    db_path: Path,
) -> tuple[tuple[str, ...], tuple[EndpointRelationship, ...]]:
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_records"):
            return (), ()
        endpoint_names = tuple(
            str(row["endpoint"])
            for row in conn.execute(
                """
                SELECT DISTINCT endpoint
                FROM endpoint_records
                ORDER BY endpoint
                """
            ).fetchall()
        )
        id_index = _endpoint_ids_from_db(conn)
        found: set[EndpointRelationship] = set()
        for row in conn.execute(
            """
            SELECT endpoint, payload_json
            FROM endpoint_records
            ORDER BY endpoint, record_id
            """
        ):
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                continue
            source_endpoint = str(row["endpoint"])
            for path, value, is_array in _iter_scalar_values(payload):
                if path in IDENTITY_PATHS:
                    continue
                ref = _clean_ref(value)
                if ref is None:
                    continue
                for target_endpoint in sorted(id_index.get(ref, ())):
                    found.add(
                        EndpointRelationship(
                            source_endpoint=source_endpoint,
                            source_path=path,
                            target_endpoint=target_endpoint,
                            array=is_array,
                        )
                    )
        return endpoint_names, tuple(
            sorted(
                found,
                key=lambda item: (item.source_endpoint, item.source_path, item.target_endpoint),
            )
        )


def _endpoint_ids_from_db(conn: sqlite3.Connection) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        """
        SELECT endpoint, record_id
        FROM endpoint_records
        ORDER BY endpoint, record_id
        """
    ):
        ref = _clean_ref(row["record_id"])
        if ref is not None:
            ids[ref].add(str(row["endpoint"]))
    return ids


def _endpoint_ids(records: dict[str, tuple[dict[str, Any], ...]]) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = defaultdict(set)
    for endpoint, endpoint_records in records.items():
        for record in endpoint_records:
            ref = _clean_ref(record.get("id"))
            if ref is not None:
                ids[ref].add(endpoint)
    return ids


def _iter_scalar_values(value: Any, path: str = "", *, in_array: bool = False):
    if isinstance(value, dict):
        for key, item in value.items():
            child_path, child_in_array = _child_path(path, str(key), in_array=in_array)
            yield from _iter_scalar_values(
                item,
                child_path,
                in_array=child_in_array,
            )
        return
    if isinstance(value, list):
        array_path = f"{path}[]" if path and not path.endswith("[]") else path
        for item in value:
            yield from _iter_scalar_values(item, array_path, in_array=True)
        return
    yield path, value, in_array or path.endswith("[]")


def _child_path(parent: str, key: str, *, in_array: bool) -> tuple[str, bool]:
    if key.isdigit():
        if not parent:
            return "[]", True
        if parent.endswith("[]"):
            return parent, True
        return f"{parent}[]", True
    if not parent:
        return key, in_array
    return f"{parent}.{key}", in_array


def _clean_ref(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if text in EMPTY_REF_VALUES else text


def _relationships_by(
    relationships: list[dict[str, Any]],
    key: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relationship in relationships:
        grouped[relationship[key]].append(relationship)
    return {
        endpoint: sorted(
            items,
            key=lambda item: (
                item["source_endpoint"],
                item["source_path"],
                item["target_endpoint"],
            ),
        )
        for endpoint, items in grouped.items()
    }


def _write_text(path: Path, content: str) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-endpoint-map-{uuid.uuid4().hex[:8]}"
