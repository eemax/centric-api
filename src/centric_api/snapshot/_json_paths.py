from __future__ import annotations

from typing import Any

from ..config import ConfigError


def field_changes(old: Any, new: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        output: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            child_path = f"{path}/{escape_json_pointer(str(key))}"
            if key not in old:
                output.append((child_path, None, new[key]))
            elif key not in new:
                output.append((child_path, old[key], None))
            else:
                output.extend(field_changes(old[key], new[key], child_path))
        return output
    if isinstance(old, list) and isinstance(new, list):
        output = []
        for index in range(max(len(old), len(new))):
            child_path = f"{path}/{index}"
            if index >= len(old):
                output.append((child_path, None, new[index]))
            elif index >= len(new):
                output.append((child_path, old[index], None))
            else:
                output.extend(field_changes(old[index], new[index], child_path))
        return output
    return [(path or "/", old, new)]


def get_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path_parts(path):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(path)
    return current


def path_exists(payload: Any, path: str) -> bool:
    try:
        get_path(payload, path)
    except (KeyError, IndexError, ValueError, TypeError):
        return False
    return True


def set_path(payload: Any, path: str, value: Any) -> None:
    parts = path_parts(path)
    if not parts:
        raise ConfigError("Cannot field-promote a whole record path.")
    parent = _parent_payload(payload, parts)
    leaf = parts[-1]
    if isinstance(parent, list):
        parent[int(leaf)] = value
    elif isinstance(parent, dict):
        parent[leaf] = value
    else:
        raise ConfigError(f"Cannot set JSON pointer path: {path}")


def delete_path(payload: Any, path: str) -> None:
    parts = path_parts(path)
    if not parts:
        raise ConfigError("Cannot field-delete a whole record path.")
    parent = _parent_payload(payload, parts)
    leaf = parts[-1]
    if isinstance(parent, list):
        del parent[int(leaf)]
    elif isinstance(parent, dict):
        parent.pop(leaf, None)
    else:
        raise ConfigError(f"Cannot delete JSON pointer path: {path}")


def escape_json_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def path_parts(path: str) -> list[str]:
    if path in {"", "/"}:
        return []
    if not path.startswith("/"):
        raise ConfigError(f"Invalid JSON pointer path: {path}")
    return [_unescape_json_pointer(part) for part in path[1:].split("/")]


def _parent_payload(payload: Any, parts: list[str]) -> Any:
    if len(parts) == 1:
        return payload
    parent_path = "/" + "/".join(escape_json_pointer(part) for part in parts[:-1])
    return get_path(payload, parent_path)


def _unescape_json_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")
