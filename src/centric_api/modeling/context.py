from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from ..store import table_exists
from ..units import UnitRegistry
from .contracts import IssueSeverity, ModelIssue

MAX_STORED_ISSUES = 50


class ModelContext:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        units: UnitRegistry,
        model_name: str,
    ) -> None:
        self.conn = conn
        self.units = units
        self.model_name = model_name
        self.issues: list[ModelIssue] = []
        self.issue_count = 0
        self.error_count = 0
        self.warning_count = 0

    def issue(
        self,
        severity: IssueSeverity,
        code: str,
        message: str,
        *,
        endpoint: str | None = None,
        record_id: str | None = None,
        sample: Any = None,
    ) -> None:
        self.issue_count += 1
        if severity == "error":
            self.error_count += 1
        elif severity == "warning":
            self.warning_count += 1
        if len(self.issues) >= MAX_STORED_ISSUES:
            return
        self.issues.append(
            ModelIssue(
                severity=severity,
                code=code,
                message=message,
                endpoint=endpoint,
                record_id=record_id,
                sample=sample,
            )
        )

    def error(
        self,
        code: str,
        message: str,
        *,
        endpoint: str | None = None,
        record_id: str | None = None,
        sample: Any = None,
    ) -> None:
        self.issue("error", code, message, endpoint=endpoint, record_id=record_id, sample=sample)

    def warning(
        self,
        code: str,
        message: str,
        *,
        endpoint: str | None = None,
        record_id: str | None = None,
        sample: Any = None,
    ) -> None:
        self.issue("warning", code, message, endpoint=endpoint, record_id=record_id, sample=sample)

    def has_errors(self) -> bool:
        return self.error_count > 0

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
            self.error(
                "missing_endpoint",
                f"Missing cached endpoint; fetch one of: {', '.join(candidates)}.",
                endpoint=candidates[0] if candidates else None,
            )
        return None

    def require_endpoints(self, endpoints: Iterable[str]) -> None:
        for endpoint in endpoints:
            self.resolve_endpoint(endpoint)

    def records(self, endpoint: str) -> list[dict[str, Any]]:
        if not table_exists(self.conn, "endpoint_records"):
            return []
        rows = self.conn.execute(
            """
            SELECT payload_json
            FROM endpoint_records
            WHERE endpoint = ?
            ORDER BY record_id
            """,
            [endpoint],
        ).fetchall()
        return [_json_dict(row["payload_json"]) for row in rows]

    def records_any(
        self, *endpoints: str, required: bool = True
    ) -> tuple[str | None, list[dict[str, Any]]]:
        endpoint = self.resolve_endpoint(*endpoints, required=required)
        if endpoint is None:
            return None, []
        return endpoint, self.records(endpoint)

    def index_by_id(self, endpoint: str) -> dict[str, dict[str, Any]]:
        return {str(record["id"]): record for record in self.records(endpoint) if record.get("id")}

    def index_by_id_any(
        self,
        *endpoints: str,
        required: bool = True,
    ) -> tuple[str | None, dict[str, dict[str, Any]]]:
        endpoint, records = self.records_any(*endpoints, required=required)
        return endpoint, {str(record["id"]): record for record in records if record.get("id")}


def _json_dict(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}
