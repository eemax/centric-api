from __future__ import annotations

from typing import Any

from .index import SwaggerField, SwaggerIndex, SwaggerOperation


def diff_swagger_indexes(previous: SwaggerIndex, current: SwaggerIndex) -> dict[str, Any]:
    operation_diff = diff_swagger_operations(previous, current)
    field_diff = diff_swagger_fields(previous, current)
    return {
        "fields": field_diff,
        "operations": operation_diff,
        "field_added_count": field_diff["added_count"],
        "field_removed_count": field_diff["removed_count"],
        "field_changed_count": field_diff["changed_count"],
        "operation_added_count": operation_diff["added_count"],
        "operation_removed_count": operation_diff["removed_count"],
        "operation_changed_count": operation_diff["changed_count"],
        "added_count": field_diff["added_count"] + operation_diff["added_count"],
        "removed_count": field_diff["removed_count"] + operation_diff["removed_count"],
        "changed_count": field_diff["changed_count"] + operation_diff["changed_count"],
    }


def diff_swagger_operations(previous: SwaggerIndex, current: SwaggerIndex) -> dict[str, Any]:
    previous_operations = _operations_by_key(previous)
    current_operations = _operations_by_key(current)
    previous_keys = set(previous_operations)
    current_keys = set(current_operations)
    added_keys = sorted(current_keys - previous_keys)
    removed_keys = sorted(previous_keys - current_keys)
    changed_keys = sorted(
        key
        for key in previous_keys & current_keys
        if previous_operations[key].fingerprint != current_operations[key].fingerprint
    )
    return {
        "added": [_operation_record(current_operations[key]) for key in added_keys],
        "removed": [_operation_record(previous_operations[key]) for key in removed_keys],
        "changed": [
            {
                "path": key[0],
                "method": key[1],
                "endpoint": current_operations[key].endpoint,
            }
            for key in changed_keys
        ],
        "added_count": len(added_keys),
        "removed_count": len(removed_keys),
        "changed_count": len(changed_keys),
    }


def diff_swagger_fields(previous: SwaggerIndex, current: SwaggerIndex) -> dict[str, Any]:
    previous_groups = _field_groups(previous)
    current_groups = _field_groups(current)
    group_keys = sorted(set(previous_groups) | set(current_groups))
    groups: list[dict[str, Any]] = []
    added_count = 0
    removed_count = 0
    changed_count = 0
    for key in group_keys:
        previous_fields = previous_groups.get(key, {})
        current_fields = current_groups.get(key, {})
        added_names = sorted(set(current_fields) - set(previous_fields))
        removed_names = sorted(set(previous_fields) - set(current_fields))
        changed_names = sorted(
            name
            for name in set(previous_fields) & set(current_fields)
            if previous_fields[name].fingerprint != current_fields[name].fingerprint
        )
        if not added_names and not removed_names and not changed_names:
            continue
        added = [_field_record(current_fields[name]) for name in added_names]
        removed = [_field_record(previous_fields[name]) for name in removed_names]
        changed = [
            _changed_field_record(previous_fields[name], current_fields[name])
            for name in changed_names
        ]
        groups.append(
            {
                "endpoint": key[0],
                "method": key[1],
                "surface": key[2],
                "path": key[3],
                "schema": key[4],
                "added": added,
                "removed": removed,
                "changed": changed,
                "added_count": len(added),
                "removed_count": len(removed),
                "changed_count": len(changed),
            }
        )
        added_count += len(added)
        removed_count += len(removed)
        changed_count += len(changed)
    return {
        "groups": groups,
        "added_count": added_count,
        "removed_count": removed_count,
        "changed_count": changed_count,
    }


def _operations_by_key(index: SwaggerIndex) -> dict[tuple[str, str], SwaggerOperation]:
    return {(operation.path, operation.method): operation for operation in index.operations}


def _field_groups(
    index: SwaggerIndex,
) -> dict[tuple[str, str, str, str, str | None], dict[str, SwaggerField]]:
    groups: dict[tuple[str, str, str, str, str | None], dict[str, SwaggerField]] = {}
    for operation in index.operations:
        if operation.request_fields:
            groups[
                (
                    operation.endpoint,
                    operation.method,
                    "request",
                    operation.path,
                    operation.request_schema,
                )
            ] = {field.name: field for field in operation.request_fields}
        if operation.response_fields:
            groups[
                (
                    operation.endpoint,
                    operation.method,
                    "response",
                    operation.path,
                    operation.response_schema,
                )
            ] = {field.name: field for field in operation.response_fields}
    return groups


def _operation_record(operation: SwaggerOperation) -> dict[str, Any]:
    return {
        "path": operation.path,
        "method": operation.method,
        "endpoint": operation.endpoint,
    }


def _field_record(field: SwaggerField) -> dict[str, Any]:
    return {
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


def _changed_field_record(previous: SwaggerField, current: SwaggerField) -> dict[str, Any]:
    changes = {}
    for key in (
        "type",
        "item_type",
        "ref",
        "object_type",
        "format",
        "enum",
        "required",
        "description",
    ):
        previous_value = getattr(previous, key)
        current_value = getattr(current, key)
        if previous_value != current_value:
            if key == "enum":
                changes[key] = _changed_enum_record(previous_value, current_value)
            else:
                changes[key] = {"from": previous_value, "to": current_value}
    return {"name": current.name, "changes": changes}


def _changed_enum_record(
    previous: tuple[Any, ...] | None, current: tuple[Any, ...] | None
) -> dict[str, Any]:
    previous_values = previous or ()
    current_values = current or ()
    previous_set = set(previous_values)
    current_set = set(current_values)
    added = [value for value in current_values if value not in previous_set]
    removed = [value for value in previous_values if value not in current_set]
    return {
        "added": added,
        "removed": removed,
        "added_count": len(added),
        "removed_count": len(removed),
    }
