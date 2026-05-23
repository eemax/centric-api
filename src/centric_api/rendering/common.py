from __future__ import annotations

import json
from typing import Any


def format_count(value: int) -> str:
    return f"{value:,}"


def signed_count(prefix: str, value: int, *, suffix: str = "") -> str:
    if not value:
        return ""
    return f"{prefix}{format_count(value)}{suffix}"


def print_rows(rows: list[dict[str, Any]], as_json: bool, *, empty_message: str) -> int:
    if as_json:
        for row in rows:
            print(json.dumps(row, default=str))
        return 0
    if not rows:
        print(empty_message)
        return 0
    for row in rows:
        print(" ".join(f"{key}={json.dumps(value, default=str)}" for key, value in row.items()))
    return 0


def print_or_json(as_json: bool, payload: dict[str, Any], message: str) -> None:
    print(json.dumps(payload, default=str) if as_json else message)
