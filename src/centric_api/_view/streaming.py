from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..store import connect_readonly, table_exists
from ..view_config import ViewColumn, ViewDefinition
from .materialize import (
    _matches_filters,
    _materialized_row,
    _quote_identifier,
    _validate_sql_identifier,
)


@dataclass
class StreamingViewRows:
    root_row_count: int
    headers: tuple[str, ...]
    columns: tuple[ViewColumn, ...]
    rows: Iterator[tuple[Any, ...]]
    row_count: int = 0


def can_stream_table_view(view: ViewDefinition) -> bool:
    return view.root.source_type == "table" and not view.joins


@contextmanager
def stream_table_view(db_path: Path, view: ViewDefinition) -> Iterator[StreamingViewRows]:
    if not can_stream_table_view(view):
        raise ConfigError(f"View {view.name!r} cannot use table streaming.")
    table_name = view.root.source_name
    _validate_sql_identifier(table_name, "view source table")
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, table_name):
            message = (
                f"View root table not found: {table_name}. "
                "Run the model that creates it first."
            )
            raise ConfigError(message)
        root_row_count = _table_row_count(conn, table_name)
        cursor = conn.execute(
            f"SELECT * FROM {_quote_identifier(table_name)} ORDER BY rowid"
        )
        stream = StreamingViewRows(
            root_row_count=root_row_count,
            headers=tuple(column.header for column in view.columns),
            columns=view.columns,
            rows=iter(()),
        )
        stream.rows = _iter_table_rows(cursor, view, stream)
        yield stream


def _table_row_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {_quote_identifier(table_name)}").fetchone()
    return int(row["count"] if row is not None else 0)


def _iter_table_rows(
    cursor: sqlite3.Cursor,
    view: ViewDefinition,
    stream: StreamingViewRows,
) -> Iterator[tuple[Any, ...]]:
    for row in cursor:
        context = {view.root.alias: dict(row)}
        if view.filters and not _matches_filters(context, view.filters):
            continue
        stream.row_count += 1
        yield _materialized_row(context, view)
