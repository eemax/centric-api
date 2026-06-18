from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..store import table_exists
from ..units import UnitRegistry
from .contracts import SnapshotRecord

REF_EMPTY_VALUES = {"", "centric:"}


class SnapshotContext:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        units: UnitRegistry,
        snapshot_name: str,
    ) -> None:
        self.conn = conn
        self.units = units
        self.snapshot_name = snapshot_name
        self._records_cache: dict[str, tuple[dict[str, Any], ...]] = {}
        self._index_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def record(
        self,
        stream: str,
        key: str,
        data: dict[str, Any],
        *,
        group: tuple[str, ...] | list[str] = (),
    ) -> SnapshotRecord:
        return SnapshotRecord(stream=stream, key=str(key), data=data, group=tuple(map(str, group)))

    def endpoint_exists(self, endpoint: str) -> bool:
        if not table_exists(self.conn, "endpoint_records"):
            return False
        row = self.conn.execute(
            """
            SELECT 1
            FROM endpoint_records
            WHERE endpoint = ?
            LIMIT 1
            """,
            [endpoint],
        ).fetchone()
        return row is not None

    def resolve_endpoint(self, *candidates: str, required: bool = True) -> str | None:
        for endpoint in candidates:
            if self.endpoint_exists(endpoint):
                return endpoint
        if required:
            choices = ", ".join(candidates)
            raise ValueError(f"Missing cached endpoint; fetch one of: {choices}.")
        return None

    def records(self, endpoint: str) -> tuple[dict[str, Any], ...]:
        if endpoint in self._records_cache:
            return self._records_cache[endpoint]
        if not table_exists(self.conn, "endpoint_records"):
            self._records_cache[endpoint] = ()
            return ()
        rows = self.conn.execute(
            """
            SELECT payload_json
            FROM endpoint_records
            WHERE endpoint = ?
            ORDER BY record_id
            """,
            [endpoint],
        ).fetchall()
        records = tuple(_json_dict(row["payload_json"]) for row in rows)
        self._records_cache[endpoint] = records
        return records

    def records_any(
        self,
        *endpoints: str,
        required: bool = True,
    ) -> tuple[str | None, tuple[dict[str, Any], ...]]:
        endpoint = self.resolve_endpoint(*endpoints, required=required)
        if endpoint is None:
            return None, ()
        return endpoint, self.records(endpoint)

    def index_by_id(self, endpoint: str) -> dict[str, dict[str, Any]]:
        if endpoint in self._index_cache:
            return self._index_cache[endpoint]
        index = {
            str(record["id"]): record
            for record in self.records(endpoint)
            if self.clean_ref(record.get("id")) is not None
        }
        self._index_cache[endpoint] = index
        return index

    def index_by_id_any(
        self,
        *endpoints: str,
        required: bool = True,
    ) -> tuple[str | None, dict[str, dict[str, Any]]]:
        endpoint = self.resolve_endpoint(*endpoints, required=required)
        if endpoint is None:
            return None, {}
        return endpoint, self.index_by_id(endpoint)

    def clean_ref(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return None if text in REF_EMPTY_VALUES else text

    def refs(self, value: Any) -> tuple[str, ...]:
        refs: list[str] = []
        self._collect_refs(value, refs)
        return tuple(dict.fromkeys(refs))

    def record_name(self, record: dict[str, Any] | None) -> str | None:
        if not record:
            return None
        for key in ("node_name", "name", "code", "display_name"):
            value = self.clean_ref(record.get(key))
            if value is not None:
                return value
        return self.clean_ref(record.get("id"))

    def value_at(self, payload: Any, path: str) -> Any:
        if not path:
            return payload
        current = payload
        for part in path.split("."):
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    return None
                current = current[index]
            else:
                return None
        return current

    def _collect_refs(self, value: Any, refs: list[str]) -> None:
        if isinstance(value, list | tuple | set):
            for item in value:
                self._collect_refs(item, refs)
            return
        if isinstance(value, dict):
            for item in value.values():
                self._collect_refs(item, refs)
            return
        ref = self.clean_ref(value)
        if ref is not None:
            refs.append(ref)


def _json_dict(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}
