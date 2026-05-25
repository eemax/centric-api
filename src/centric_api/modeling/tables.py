from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..config import ConfigError
from .contracts import ModelColumn, ModelIssue, ModelOutput, ModelRunSummary

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQL_TYPES = {
    "text": "TEXT",
    "number": "REAL",
    "integer": "INTEGER",
    "boolean": "INTEGER",
    "json": "TEXT",
}


def ensure_model_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_runs (
            run_id TEXT PRIMARY KEY,
            model_name TEXT NOT NULL,
            title TEXT NOT NULL,
            output_table TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            issue_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_run_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            endpoint TEXT,
            record_id TEXT,
            sample_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES model_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_model_runs_model_finished
        ON model_runs(model_name, finished_at);

        CREATE INDEX IF NOT EXISTS idx_model_run_issues_run
        ON model_run_issues(run_id, severity);
        """
    )


def replace_output_table(
    conn: sqlite3.Connection,
    *,
    output_table: str,
    output: ModelOutput,
    run_id: str,
) -> None:
    _validate_identifier(output_table, "model output table")
    column_names = [column.name for column in output.columns]
    if not column_names:
        raise ConfigError("Model output must define at least one column.")
    if len(set(column_names)) != len(column_names):
        raise ConfigError("Model output column names must be unique.")
    for column in output.columns:
        _validate_identifier(column.name, "model output column")

    temp_table = f"__tmp_{output_table}_{_safe_suffix(run_id)}"
    columns_sql = ", ".join(
        f"{_quote(column.name)} {SQL_TYPES[column.type]}" for column in output.columns
    )
    conn.execute(f"DROP TABLE IF EXISTS {_quote(temp_table)}")
    conn.execute(f"CREATE TABLE {_quote(temp_table)} ({columns_sql})")
    if output.rows:
        placeholders = ", ".join("?" for _ in output.columns)
        insert_sql = (
            f"INSERT INTO {_quote(temp_table)} "
            f"({', '.join(_quote(name) for name in column_names)}) "
            f"VALUES ({placeholders})"
        )
        conn.executemany(
            insert_sql,
            [
                [_sql_value(row.get(column.name), column) for column in output.columns]
                for row in output.rows
            ],
        )
    conn.execute(f"DROP TABLE IF EXISTS {_quote(output_table)}")
    conn.execute(f"ALTER TABLE {_quote(temp_table)} RENAME TO {_quote(output_table)}")


def record_model_run(conn: sqlite3.Connection, summary: ModelRunSummary) -> None:
    conn.execute(
        """
        INSERT INTO model_runs (
            run_id, model_name, title, output_table, action, status,
            started_at, finished_at, row_count, issue_count, error_count, warning_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            summary.run_id,
            summary.model_name,
            summary.title,
            summary.output_table,
            summary.action,
            summary.status,
            _utc_iso(),
            _utc_iso(),
            summary.row_count,
            summary.issue_count,
            summary.error_count,
            summary.warning_count,
        ],
    )
    insert_issues(conn, summary.run_id, summary.issues)


def insert_issues(
    conn: sqlite3.Connection,
    run_id: str,
    issues: Iterable[ModelIssue],
) -> None:
    rows = [
        [
            run_id,
            issue.severity,
            issue.code,
            issue.message,
            issue.endpoint,
            issue.record_id,
            json.dumps(issue.sample, default=str) if issue.sample is not None else None,
            _utc_iso(),
        ]
        for issue in issues
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO model_run_issues (
            run_id, severity, code, message, endpoint, record_id, sample_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def issue_record(issue: ModelIssue) -> dict[str, Any]:
    return asdict(issue)


def _sql_value(value: Any, column: ModelColumn) -> Any:
    if value is None:
        return None
    if column.type == "json":
        return json.dumps(value, default=str)
    if column.type == "boolean":
        return int(bool(value))
    if column.type == "integer":
        return int(value)
    if column.type == "number":
        if isinstance(value, Decimal):
            return float(value)
        return value
    return str(value)


def _validate_identifier(value: str, label: str) -> None:
    if not IDENTIFIER_PATTERN.match(value):
        raise ConfigError(f"{label} must be a SQLite-safe identifier.")


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _safe_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()
