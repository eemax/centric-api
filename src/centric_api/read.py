"""Read a centric-api database from another program: the supported way to consume it.

    from centric_api.read import Reader

    with Reader("centric.db") as db:
        for style in db.records("styles", fields=["node_name", "active", "parent_season"]):
            ...
        seq = db.change_seq()                           # remember this ...
        changed = db.changes(since=seq_from_last_time)  # ... and ask later what moved

Records are the JSON payloads exactly as Centric returned them (bronze), as dicts. With
``fields``, SQLite extracts just those top-level fields in C, so a consumer that needs 10
of an endpoint's 100 fields never parses (or holds) the other 90. A field missing from a
payload is missing from the dict too, so ``"x" in record`` keeps its meaning. ``id`` is
always included. Deleted records (flagged by ``fetch --full --allow-delete``) are left out
unless ``include_deleted=True``.

The connection is read-only (SQLite WAL), so reading never blocks or disturbs a running
fetch. Prefer this over querying the tables directly: it checks the schema version, and
the tables' layout may change between versions while this interface stays.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import (
    DatabaseError,
    connect,
    latest_change_seq,
    stored_endpoints,
    table_exists,
)


@dataclass(frozen=True)
class EndpointChanges:
    """Records of one endpoint that changed after a given change sequence number."""

    endpoint: str
    changed: int  # inserted, updated or restored, and currently present
    deleted: int  # flagged deleted
    last_seq: int  # highest change_seq among this endpoint's records (0 if none)

    @property
    def total(self) -> int:
        return self.changed + self.deleted


class Reader:
    """Read-only access to a centric-api database. Use as a context manager."""

    def __init__(self, database: str | Path) -> None:
        path = Path(database).expanduser()
        if not path.is_file():
            raise DatabaseError(f"Database not found: {path}")
        self.path = path
        self.conn = connect(path, readonly=True)
        # connect() refuses foreign databases and other schema versions; an empty or
        # half-created file gets past it, so check for the schema itself too.
        if self._info("schema_version") is None or not table_exists(self.conn, "bronze_records"):
            self.conn.close()
            raise DatabaseError(f"{path} is not a centric-api database.")
        # What this SQLite can extract by itself; anything it can't is done in Python.
        self._arrow, self._escaped_keys = _probe_json_support(self.conn)

    def __enter__(self) -> Reader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ records

    def endpoints(self) -> list[str]:
        """Endpoints that have records stored, sorted."""
        return stored_endpoints(self.conn)

    def count(self, endpoint: str, *, include_deleted: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM bronze_records WHERE endpoint = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        return int(self.conn.execute(sql, [endpoint]).fetchone()[0])

    def records(
        self,
        endpoint: str,
        *,
        fields: Iterable[str] | None = None,
        include_deleted: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield an endpoint's records, ordered by id. Streams: nothing is held between rows."""
        where = "endpoint = ?" + ("" if include_deleted else " AND deleted = 0")
        if fields is None:
            for (payload,) in self.conn.execute(
                f"SELECT payload FROM bronze_records WHERE {where} ORDER BY id", [endpoint]
            ):
                yield _json_object(payload)
            return
        wanted = _field_list(fields)
        in_sql = [name for name in wanted if self._can_extract(name)]
        in_python = [name for name in wanted if name not in in_sql]
        columns = ["payload -> ?" for _ in in_sql] + (["payload"] if in_python else [])
        params = [_json_path(name) for name in in_sql] + [endpoint]
        sql = f"SELECT {', '.join(columns)} FROM bronze_records WHERE {where} ORDER BY id"
        loads = json.loads
        for row in self.conn.execute(sql, params):
            found = {
                key: loads(value)
                for key, value in zip(in_sql, row, strict=False)
                if value is not None
            }
            if in_python:
                payload = _json_object(row[-1])
                found.update((key, payload[key]) for key in in_python if key in payload)
            yield {key: found[key] for key in wanted if key in found}

    def records_by_id(
        self,
        endpoint: str,
        *,
        fields: Iterable[str] | None = None,
        include_deleted: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """All of an endpoint's records keyed by id (in id order)."""
        return {
            str(record["id"]): record
            for record in self.records(endpoint, fields=fields, include_deleted=include_deleted)
            if record.get("id") is not None
        }

    # ------------------------------------------------------------------ changes

    def change_seq(self) -> int:
        """The sequence number of the latest change to any record (0 when empty).

        Every insert, update, restore or deletion stamps the record with a new, higher
        number; records a fetch sees unchanged keep theirs. So this only moves when data
        actually changed, and ``changes(since=...)`` with a stored value says what did.
        """
        return latest_change_seq(self.conn)

    def changes(
        self, *, since: int = 0, endpoints: Iterable[str] | None = None
    ) -> list[EndpointChanges]:
        """Per endpoint, how many records changed after change sequence ``since``.

        Store ``change_seq()`` after processing; next time, ``changes(since=stored)`` says
        whether anything you read has moved (``total == 0`` everywhere: nothing to redo).
        """
        names = sorted(set(endpoints)) if endpoints is not None else self.endpoints()
        result = []
        for name in names:
            # Index range on (endpoint, change_seq): touches only records that changed.
            changed, deleted = self.conn.execute(
                """
                SELECT COALESCE(SUM(deleted = 0), 0), COALESCE(SUM(deleted = 1), 0)
                FROM bronze_records WHERE endpoint = ? AND change_seq > ?
                """,
                [name, since],
            ).fetchone()
            result.append(
                EndpointChanges(
                    endpoint=name,
                    changed=int(changed),
                    deleted=int(deleted),
                    last_seq=latest_change_seq(self.conn, [name]),
                )
            )
        return result

    # ------------------------------------------------------------------ internals

    def _can_extract(self, name: str) -> bool:
        return self._arrow and (self._escaped_keys or not any(ch in name for ch in '"\\'))

    def _info(self, key: str) -> str | None:
        if not table_exists(self.conn, "meta_info"):
            return None
        row = self.conn.execute("SELECT value FROM meta_info WHERE key = ?", [key]).fetchone()
        return row[0] if row else None


def _probe_json_support(conn: sqlite3.Connection) -> tuple[bool, bool]:
    """(can use ``->`` at all, can address keys that need escaping: ``"`` or ``\\``).

    ``->`` needs SQLite 3.38. Escapes inside a quoted path label only work in newer
    releases: 3.45 returns NULL for ``$."a\\"b"``, which would silently drop the field. So
    test the actual library instead of trusting a version number.
    """
    try:
        plain, escaped = conn.execute(
            "SELECT ? -> ?, ? -> ?",
            [
                json.dumps({"a.b": 1}),
                _json_path("a.b"),
                json.dumps({'q"b\\s': 2}),
                _json_path('q"b\\s'),
            ],
        ).fetchone()
    except sqlite3.OperationalError:
        return False, False
    return plain == "1", escaped == "2"


def _field_list(fields: Iterable[str]) -> list[str]:
    if isinstance(fields, str | bytes | Mapping):
        raise TypeError("fields must be an iterable of field names, not a string or mapping")
    wanted = ["id"]
    for name in fields:
        if not isinstance(name, str) or not name:
            raise TypeError(f"field names must be non-empty strings, got {name!r}")
        if name not in wanted:
            wanted.append(name)
    return wanted


def _json_path(name: str) -> str:
    # Quote the key so any character is safe: $."node_name", $."a.b", $."say \"hi\""
    return '$."' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _json_object(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    return value if isinstance(value, dict) else {}
