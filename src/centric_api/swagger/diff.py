from __future__ import annotations

from typing import Any

from .index import SwaggerIndex, SwaggerOperation


def diff_swagger_indexes(previous: SwaggerIndex, current: SwaggerIndex) -> dict[str, Any]:
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


def _operations_by_key(index: SwaggerIndex) -> dict[tuple[str, str], SwaggerOperation]:
    return {(operation.path, operation.method): operation for operation in index.operations}


def _operation_record(operation: SwaggerOperation) -> dict[str, Any]:
    return {
        "path": operation.path,
        "method": operation.method,
        "endpoint": operation.endpoint,
    }
