from __future__ import annotations

from typing import Any


def empty_diff() -> dict[str, Any]:
    return {
        "fields": {
            "groups": [],
            "added_count": 0,
            "removed_count": 0,
            "changed_count": 0,
        },
        "operations": {
            "added": [],
            "removed": [],
            "changed": [],
            "added_count": 0,
            "removed_count": 0,
            "changed_count": 0,
        },
        "field_added_count": 0,
        "field_removed_count": 0,
        "field_changed_count": 0,
        "operation_added_count": 0,
        "operation_removed_count": 0,
        "operation_changed_count": 0,
        "added_count": 0,
        "removed_count": 0,
        "changed_count": 0,
    }


def diff_count(
    diff: dict[str, Any],
    *,
    fields_only: bool = False,
    operations_only: bool = False,
) -> int:
    total = 0
    if not operations_only:
        fields = field_diff(diff)
        total += int(fields.get("added_count", 0))
        total += int(fields.get("removed_count", 0))
        total += int(fields.get("changed_count", 0))
    if not fields_only:
        operations = operation_diff(diff)
        total += int(operations.get("added_count", 0))
        total += int(operations.get("removed_count", 0))
        total += int(operations.get("changed_count", 0))
    return total


def indexed_field_rows(index: Any) -> list[dict[str, Any]]:
    rows = [row for operation in index.operations for row in _field_rows(operation)]
    return [{**row, "index": field_index} for field_index, row in enumerate(rows, start=1)]


def _field_rows(operation: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for surface, schema, fields in (
        ("request", operation.request_schema, operation.request_fields),
        ("response", operation.response_schema, operation.response_fields),
    ):
        for field in fields:
            rows.append(
                {
                    "endpoint": operation.endpoint,
                    "method": operation.method,
                    "path": operation.path,
                    "surface": surface,
                    "schema": schema,
                    "name": field.name,
                    "type": field.type,
                    "item_type": field.item_type,
                    "ref": field.ref,
                    "object_type": field.object_type,
                    "format": field.format,
                    "enum": field.enum,
                    "required": field.required,
                    "description": field.description,
                }
            )
    return rows


def operation_field_groups(
    operation: Any,
    *,
    required_only: bool = False,
) -> list[tuple[str, str | None, tuple[Any, ...]]]:
    groups = []
    request_fields = _filter_required(operation.request_fields, required_only=required_only)
    response_fields = _filter_required(operation.response_fields, required_only=required_only)
    if request_fields:
        groups.append(("request", operation.request_schema, request_fields))
    if response_fields:
        groups.append(("response", operation.response_schema, response_fields))
    return groups


def _filter_required(fields: tuple[Any, ...], *, required_only: bool) -> tuple[Any, ...]:
    if not required_only:
        return fields
    return tuple(field for field in fields if field.required)


def matches_filters(
    endpoint: str,
    method: str,
    path: str,
    endpoint_filter: str | None,
    method_filter: str,
    include_nested: bool,
) -> bool:
    if endpoint_filter and endpoint != endpoint_filter:
        return False
    if endpoint_filter and not include_nested and not _is_root_endpoint_path(path):
        return False
    return method_filter == "all" or method == method_filter.upper()


def _is_root_endpoint_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[0] in {"v1", "v2", "v3"}:
        parts = parts[1:]
    return len(parts) == 1


def filter_diff(
    diff: dict[str, Any],
    *,
    endpoint: str | None,
    method: str,
    include_nested: bool,
) -> dict[str, Any]:
    fields = field_diff(diff)
    operations = operation_diff(diff)
    filtered_fields = _filter_field_diff(
        fields,
        endpoint=endpoint,
        method=method,
        include_nested=include_nested,
    )
    filtered_operations = _filter_operation_diff(
        operations,
        endpoint=endpoint,
        method=method,
        include_nested=include_nested,
    )
    return {
        "fields": filtered_fields,
        "operations": filtered_operations,
        "field_added_count": filtered_fields["added_count"],
        "field_removed_count": filtered_fields["removed_count"],
        "field_changed_count": filtered_fields["changed_count"],
        "operation_added_count": filtered_operations["added_count"],
        "operation_removed_count": filtered_operations["removed_count"],
        "operation_changed_count": filtered_operations["changed_count"],
        "added_count": filtered_fields["added_count"] + filtered_operations["added_count"],
        "removed_count": filtered_fields["removed_count"] + filtered_operations["removed_count"],
        "changed_count": filtered_fields["changed_count"] + filtered_operations["changed_count"],
    }


def diff_view(
    diff: dict[str, Any],
    *,
    fields_only: bool,
    operations_only: bool,
) -> dict[str, Any]:
    if fields_only:
        operations = empty_diff()["operations"]
        fields = field_diff(diff)
    elif operations_only:
        fields = empty_diff()["fields"]
        operations = operation_diff(diff)
    else:
        fields = field_diff(diff)
        operations = operation_diff(diff)
    return {
        "fields": fields,
        "operations": operations,
        "field_added_count": fields["added_count"],
        "field_removed_count": fields["removed_count"],
        "field_changed_count": fields["changed_count"],
        "operation_added_count": operations["added_count"],
        "operation_removed_count": operations["removed_count"],
        "operation_changed_count": operations["changed_count"],
        "added_count": fields["added_count"] + operations["added_count"],
        "removed_count": fields["removed_count"] + operations["removed_count"],
        "changed_count": fields["changed_count"] + operations["changed_count"],
    }


def _filter_field_diff(
    fields: dict[str, Any],
    *,
    endpoint: str | None,
    method: str,
    include_nested: bool,
) -> dict[str, Any]:
    groups = [
        group
        for group in fields.get("groups", [])
        if matches_filters(
            group["endpoint"],
            group["method"],
            group["path"],
            endpoint,
            method,
            include_nested,
        )
    ]
    return {
        "groups": groups,
        "added_count": sum(int(group.get("added_count", 0)) for group in groups),
        "removed_count": sum(int(group.get("removed_count", 0)) for group in groups),
        "changed_count": sum(int(group.get("changed_count", 0)) for group in groups),
    }


def _filter_operation_diff(
    operations: dict[str, Any],
    *,
    endpoint: str | None,
    method: str,
    include_nested: bool,
) -> dict[str, Any]:
    added = [
        row
        for row in operations.get("added", [])
        if matches_filters(
            row["endpoint"], row["method"], row["path"], endpoint, method, include_nested
        )
    ]
    removed = [
        row
        for row in operations.get("removed", [])
        if matches_filters(
            row["endpoint"], row["method"], row["path"], endpoint, method, include_nested
        )
    ]
    changed = [
        row
        for row in operations.get("changed", [])
        if matches_filters(
            row["endpoint"], row["method"], row["path"], endpoint, method, include_nested
        )
    ]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
    }


def field_diff(diff: dict[str, Any]) -> dict[str, Any]:
    fields = diff.get("fields")
    if isinstance(fields, dict):
        return fields
    return {"groups": [], "added_count": 0, "removed_count": 0, "changed_count": 0}


def operation_diff(diff: dict[str, Any]) -> dict[str, Any]:
    operations = diff.get("operations")
    if isinstance(operations, dict):
        return operations
    if {"added", "removed", "changed"} <= set(diff):
        return {
            "added": diff.get("added", []),
            "removed": diff.get("removed", []),
            "changed": diff.get("changed", []),
            "added_count": int(diff.get("added_count", 0)),
            "removed_count": int(diff.get("removed_count", 0)),
            "changed_count": int(diff.get("changed_count", 0)),
        }
    return {
        "added": [],
        "removed": [],
        "changed": [],
        "added_count": 0,
        "removed_count": 0,
        "changed_count": 0,
    }


def field_schema_count(index: Any | None) -> int:
    if index is None:
        return 0
    return len(_field_schema_groups(index))


def field_count(index: Any | None) -> int:
    if index is None:
        return 0
    return sum(len(fields) for fields in _field_schema_groups(index).values())


def _field_schema_groups(index: Any) -> dict[str, tuple[Any, ...]]:
    groups: dict[str, tuple[Any, ...]] = {}
    for operation in index.operations:
        for surface, schema, fields in operation_field_groups(operation):
            key = schema or f"{operation.method} {operation.path} {surface}"
            groups.setdefault(key, fields)
    return groups
