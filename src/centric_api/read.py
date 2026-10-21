"""Read a centric-api database from another program: the supported way to consume it.

    from centric_api.read import Reader

    with Reader("centric.db") as db:
        for style in db.records("styles", fields=["node_name", "active", "parent_season"]):
            ...
        seq = db.change_seq()                           # remember this ...
        changed = db.changes(since=seq_from_last_time)  # ... and ask later what moved

Records are the JSON payloads exactly as Centric returned them (bronze), as dicts. With
``fields``, each payload is parsed (with orjson) and only those top-level fields are kept;
the rest is freed at once, so a consumer that needs 10 of an endpoint's 100 fields never
holds the other 90. A field missing from a payload is missing from the dict too, so
``"x" in record`` keeps its meaning, and a JSON null stays None. ``id`` comes first, then
the fields in the order asked. Deleted records (flagged by ``fetch --full
--allow-delete``) are left out unless ``include_deleted=True``.

The connection is read-only (SQLite WAL), so reading never blocks or disturbs a running
fetch. Prefer this over querying the tables directly: it checks the schema version, and
the tables' layout may change between versions while this interface stays.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
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

try:
    import orjson
except ImportError:  # pragma: no cover - orjson is a dependency; this keeps a broken
    orjson = None  # install working (slower) instead of failing outright


def _loads_stdlib(payload: str) -> Any:
    return json.loads(payload)


def _loads_orjson(payload: str) -> Any:
    try:
        return orjson.loads(payload)
    except orjson.JSONDecodeError:
        # orjson is strict where json isn't: NaN/Infinity, integers beyond 64 bits.
        return json.loads(payload)


# Parsing whole payloads in Python and keeping the wanted keys beats extracting each field
# in SQLite: 364k records with ~10 fields each in 1.0 s with orjson (2.1 s with json) vs
# 3.8 s with one `payload -> '$.field'` per field. The per-value call overhead dominates,
# not the parsing.
_loads: Callable[[str], Any] = _loads_orjson if orjson is not None else _loads_stdlib


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
        wanted = None if fields is None else _field_list(fields)
        where = "endpoint = ?" + ("" if include_deleted else " AND deleted = 0")
        rows = self.conn.execute(
            f"SELECT payload FROM bronze_records WHERE {where} ORDER BY id", [endpoint]
        )
        for (payload,) in rows:
            record = _json_object(payload)
            if wanted is None:
                yield record
            else:
                # Iterate the few wanted keys, not the payload's dozens.
                yield {key: record[key] for key in wanted if key in record}

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

    def _info(self, key: str) -> str | None:
        if not table_exists(self.conn, "meta_info"):
            return None
        row = self.conn.execute("SELECT value FROM meta_info WHERE key = ?", [key]).fetchone()
        return row[0] if row else None


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


def _json_object(payload: str) -> dict[str, Any]:
    value = _loads(payload)
    return value if isinstance(value, dict) else {}
