from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..store import table_exists

REF_EMPTY_VALUES = {"", "centric:"}
DEFAULT_LABEL_FIELDS = ("node_name", "name", "code", "display_name")


class SnapshotReviewDisplayContext:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._records_cache: dict[str, tuple[dict[str, Any], ...]] = {}
        self._index_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def record(self, endpoint: str, record_id: Any) -> dict[str, Any] | None:
        clean_id = self.clean_ref(record_id)
        if clean_id is None:
            return None
        return self.index_by_id(endpoint).get(clean_id)

    def display_ref(
        self,
        endpoint: str,
        record_id: Any,
        *,
        label_fields: tuple[str, ...] = DEFAULT_LABEL_FIELDS,
    ) -> dict[str, Any] | None:
        clean_id = self.clean_ref(record_id)
        if clean_id is None:
            return None
        record = self.record(endpoint, clean_id)
        label = self.record_label(record, fields=label_fields) if record else clean_id
        payload: dict[str, Any] = {
            "endpoint": endpoint,
            "id": clean_id,
            "label": label,
        }
        if record is None:
            payload["missing"] = True
        return payload

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

    def clean_ref(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return None if text in REF_EMPTY_VALUES else text

    def record_label(
        self,
        record: dict[str, Any] | None,
        *,
        fields: tuple[str, ...] = DEFAULT_LABEL_FIELDS,
    ) -> str | None:
        if not record:
            return None
        for key in fields:
            value = self.clean_ref(record.get(key))
            if value is not None:
                return value
        return self.clean_ref(record.get("id"))


def _json_dict(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}
