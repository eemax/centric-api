from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import EndpointSpec
from .db import iso, quote, table_exists, transaction, utc_now
from .swagger import EXCLUDED_FIELDS, FieldPlan, endpoint_plan

ID_COLUMN = "id"
MODIFIED_AT_FIELD = "_modified_at"
BOOKKEEPING_COLUMNS = ("_row_deleted", "_row_deleted_at", "_row_updated_at")
SQL_TYPES = {
    "text": "TEXT",
    "datetime": "TEXT",
    "json": "TEXT",
    "integer": "INTEGER",
    "boolean": "INTEGER",
    "real": "REAL",
}
BATCH_SIZE = 1000
MAX_REBUILDS = 50


@dataclass
class RegisteredField:
    field: str
    ordinal: int
    kind: str
    value_type: str
    spec_type: str | None
    object_type: str | None
    table_name: str
    column_name: str | None
    in_spec: bool


@dataclass
class SilverResult:
    endpoint: str
    status: str  # "synced", "rebuilt" or "skipped"
    rows: int = 0
    notes: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)


class _Widen(Exception):
    def __init__(self, field_name: str, value_type: str) -> None:
        super().__init__(field_name)
        self.field_name = field_name
        self.value_type = value_type


def main_table(endpoint: str) -> str:
    return f"silver_{endpoint}"


def sync_endpoint(
    conn: sqlite3.Connection,
    endpoint: EndpointSpec,
    specs: dict[str, dict[str, Any]],
    *,
    force_rebuild: bool = False,
) -> SilverResult:
    """Bring an endpoint's silver tables up to date with bronze and the stored spec.

    The field registry only grows: new spec fields are added, fields that leave the spec keep
    their column, and a type that the data contradicts widens (integer to real, anything else
    to text). Any registry change rebuilds the endpoint's silver tables from bronze in one
    transaction; otherwise only records changed since the last sync are rewritten.
    """
    result = SilverResult(endpoint.name, "synced")
    plan, note = endpoint_plan(specs, endpoint)
    if note:
        result.notes.append(note)
    with transaction(conn):
        registry = _load_registry(conn, endpoint.name)
        if plan is None and not registry:
            result.status = "skipped"
            return result
        if plan is not None:
            _merge_plan(conn, endpoint.name, registry, plan)
            registry = _load_registry(conn, endpoint.name)

    rebuild = force_rebuild or _needs_rebuild(conn, endpoint.name, registry)
    for _attempt in range(MAX_REBUILDS):
        try:
            with transaction(conn):
                if rebuild:
                    result.rows = _rebuild(conn, endpoint.name, registry)
                    result.status = "rebuilt"
                else:
                    result.rows = _apply_changes(conn, endpoint.name, registry, full=False)
        except _Widen as widen:
            with transaction(conn):
                conn.execute(
                    "UPDATE meta_silver_fields SET value_type = ? WHERE endpoint = ? AND field = ?",
                    [widen.value_type, endpoint.name, widen.field_name],
                )
            result.notes.append(f"widened {widen.field_name} to {widen.value_type}")
            registry = _load_registry(conn, endpoint.name)
            rebuild = True
            continue
        break
    else:  # pragma: no cover - each widening moves a field up a finite type lattice
        raise RuntimeError(f"{endpoint.name}: silver types did not settle.")
    result.unmapped = [
        row["field"]
        for row in conn.execute(
            "SELECT field FROM meta_unmapped_fields WHERE endpoint = ? ORDER BY field",
            [endpoint.name],
        )
    ]
    return result


def _merge_plan(
    conn: sqlite3.Connection,
    endpoint: str,
    registry: list[RegisteredField],
    plan: list[FieldPlan],
) -> None:
    known = {item.field: item for item in registry}
    planned = {item.field: item for item in plan}
    if MODIFIED_AT_FIELD not in planned and MODIFIED_AT_FIELD not in known:
        planned[MODIFIED_AT_FIELD] = FieldPlan(MODIFIED_AT_FIELD, "column", "text", "system", None)
    used_columns = {name.casefold() for name in (ID_COLUMN, *BOOKKEEPING_COLUMNS)}
    used_tables: set[str] = {main_table(endpoint).casefold()}
    for item in registry:
        if item.column_name:
            used_columns.add(item.column_name.casefold())
        if item.kind != "column":
            used_tables.add(item.table_name.casefold())
    next_ordinal = max((item.ordinal for item in registry), default=0) + 1
    now = iso(utc_now())

    for name, current in known.items():
        incoming = planned.get(name)
        if incoming is None:
            if current.in_spec:
                conn.execute(
                    "UPDATE meta_silver_fields SET in_spec = 0 WHERE endpoint = ? AND field = ?",
                    [endpoint, name],
                )
            continue
        value_type = current.value_type
        if incoming.kind != current.kind:
            if current.kind == "column":
                value_type = "json"
        elif incoming.value_type != current.value_type:
            value_type = _widest(current.value_type, incoming.value_type)
        conn.execute(
            """
            UPDATE meta_silver_fields
            SET value_type = ?, spec_type = ?, object_type = ?, in_spec = 1
            WHERE endpoint = ? AND field = ?
            """,
            [value_type, incoming.spec_type, incoming.object_type, endpoint, name],
        )

    for name, incoming in planned.items():
        if name in known or name == ID_COLUMN:
            continue
        if incoming.kind == "column":
            column_name = _unique(name, used_columns)
            table_name = main_table(endpoint)
        else:
            column_name = None
            table_name = _unique(f"{main_table(endpoint)}__{name}", used_tables)
        conn.execute(
            """
            INSERT INTO meta_silver_fields (
                endpoint, field, ordinal, kind, value_type, spec_type, object_type, table_name,
                column_name, in_spec, added_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                endpoint,
                name,
                next_ordinal,
                incoming.kind,
                incoming.value_type,
                incoming.spec_type,
                incoming.object_type,
                table_name,
                column_name,
                0 if incoming.spec_type == "system" else 1,
                now,
            ],
        )
        next_ordinal += 1


def _load_registry(conn: sqlite3.Connection, endpoint: str) -> list[RegisteredField]:
    return [
        RegisteredField(
            field=row["field"],
            ordinal=row["ordinal"],
            kind=row["kind"],
            value_type=row["value_type"],
            spec_type=row["spec_type"],
            object_type=row["object_type"],
            table_name=row["table_name"],
            column_name=row["column_name"],
            in_spec=bool(row["in_spec"]),
        )
        for row in conn.execute(
            "SELECT * FROM meta_silver_fields WHERE endpoint = ? ORDER BY ordinal", [endpoint]
        )
    ]


def _plan_sha256(registry: list[RegisteredField]) -> str:
    shape = [
        [item.field, item.kind, item.value_type, item.table_name, item.column_name, item.ordinal]
        for item in registry
    ]
    return hashlib.sha256(json.dumps(shape).encode("utf-8")).hexdigest()


def _needs_rebuild(
    conn: sqlite3.Connection, endpoint: str, registry: list[RegisteredField]
) -> bool:
    state = conn.execute(
        "SELECT plan_sha256 FROM meta_silver_state WHERE endpoint = ?", [endpoint]
    ).fetchone()
    if state is None or state["plan_sha256"] != _plan_sha256(registry):
        return True
    tables = {main_table(endpoint)} | {i.table_name for i in registry if i.kind != "column"}
    return not all(table_exists(conn, name) for name in tables)


def _rebuild(conn: sqlite3.Connection, endpoint: str, registry: list[RegisteredField]) -> int:
    for name in _endpoint_tables(conn, endpoint):
        conn.execute(f"DROP TABLE IF EXISTS {quote(name)}")
    columns = [i for i in registry if i.kind == "column"]
    column_sql = ",\n    ".join(
        [
            f"{quote(ID_COLUMN)} TEXT PRIMARY KEY",
            '"_row_deleted" INTEGER NOT NULL',
            '"_row_deleted_at" TEXT',
            '"_row_updated_at" TEXT NOT NULL',
            *(f"{quote(i.column_name or i.field)} {SQL_TYPES[i.value_type]}" for i in columns),
        ]
    )
    conn.execute(f"CREATE TABLE {quote(main_table(endpoint))} (\n    {column_sql}\n)")
    for item in registry:
        if item.kind == "column":
            continue
        conn.execute(
            f"""
            CREATE TABLE {quote(item.table_name)} (
                "id" TEXT NOT NULL,
                "position" INTEGER NOT NULL,
                "key" TEXT,
                "value" {SQL_TYPES[item.value_type]},
                PRIMARY KEY ("id", "position")
            )
            """
        )
        conn.execute(
            # Index names share SQLite's namespace with tables; silver tables never start
            # with "idx_", so this cannot collide with a link table.
            f"CREATE INDEX {quote('idx_' + item.table_name + '__value')} "
            f'ON {quote(item.table_name)} ("value")'
        )
    conn.execute("DELETE FROM meta_unmapped_fields WHERE endpoint = ?", [endpoint])
    conn.execute("DELETE FROM meta_silver_state WHERE endpoint = ?", [endpoint])
    return _apply_changes(conn, endpoint, registry, full=True)


def _endpoint_tables(conn: sqlite3.Connection, endpoint: str) -> list[str]:
    prefix = main_table(endpoint)
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND (name = ? OR name LIKE ? ESCAPE '\\')",
        [prefix, prefix.replace("_", "\\_") + "\\_\\_%"],
    )
    return [row["name"] for row in rows]


def _apply_changes(
    conn: sqlite3.Connection, endpoint: str, registry: list[RegisteredField], *, full: bool
) -> int:
    state = conn.execute(
        "SELECT synced_seq FROM meta_silver_state WHERE endpoint = ?", [endpoint]
    ).fetchone()
    since = 0 if full or state is None else int(state["synced_seq"])
    top = conn.execute(
        "SELECT COALESCE(MAX(change_seq), 0) AS seq FROM bronze_records WHERE endpoint = ?",
        [endpoint],
    ).fetchone()["seq"]
    columns = [i for i in registry if i.kind == "column"]
    links = [i for i in registry if i.kind != "column"]
    mapped = {i.field for i in registry} | {ID_COLUMN} | EXCLUDED_FIELDS
    main = quote(main_table(endpoint))
    column_names = [ID_COLUMN, *BOOKKEEPING_COLUMNS, *(i.column_name or i.field for i in columns)]
    insert_main = (
        f"INSERT OR REPLACE INTO {main} ({', '.join(quote(c) for c in column_names)}) "
        f"VALUES ({', '.join('?' for _ in column_names)})"
    )
    unmapped: set[str] = set()
    count = 0
    for batch in _bronze_batches(conn, endpoint, since):
        main_rows = []
        link_rows: dict[str, list[tuple[Any, ...]]] = {i.table_name: [] for i in links}
        ids = []
        for row in batch:
            payload = json.loads(row["payload"])
            record_id = row["id"]
            ids.append(record_id)
            unmapped.update(key for key in payload if key not in mapped)
            values = [record_id, row["deleted"], row["deleted_at"], row["updated_at"]]
            for item in columns:
                values.append(_coerce(item, payload.get(item.field)))
            main_rows.append(values)
            for item in links:
                for position, (key, value) in enumerate(_elements(item, payload.get(item.field))):
                    link_rows[item.table_name].append(
                        (record_id, position, key, _coerce(item, value))
                    )
        if not full:
            for item in links:
                conn.executemany(
                    f'DELETE FROM {quote(item.table_name)} WHERE "id" = ?',
                    [(record_id,) for record_id in ids],
                )
        conn.executemany(insert_main, main_rows)
        for table_name, rows in link_rows.items():
            if rows:
                conn.executemany(
                    f'INSERT INTO {quote(table_name)} ("id", "position", "key", "value") '
                    "VALUES (?, ?, ?, ?)",
                    rows,
                )
        count += len(batch)
    now = iso(utc_now())
    if unmapped:
        conn.executemany(
            """
            INSERT INTO meta_unmapped_fields (endpoint, field, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (endpoint, field) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            [(endpoint, name, now, now) for name in sorted(unmapped)],
        )
    conn.execute(
        "DELETE FROM meta_unmapped_fields WHERE endpoint = ? AND field IN "
        "(SELECT field FROM meta_silver_fields WHERE endpoint = ?)",
        [endpoint, endpoint],
    )
    conn.execute(
        """
        INSERT INTO meta_silver_state (endpoint, synced_seq, plan_sha256, rebuilt_at, synced_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (endpoint) DO UPDATE SET
            synced_seq = excluded.synced_seq, plan_sha256 = excluded.plan_sha256,
            synced_at = excluded.synced_at
        """,
        [endpoint, top, _plan_sha256(registry), now, now],
    )
    return count


def _bronze_batches(
    conn: sqlite3.Connection, endpoint: str, since: int
) -> Iterator[list[sqlite3.Row]]:
    cursor = conn.execute(
        """
        SELECT id, payload, deleted, deleted_at, updated_at
        FROM bronze_records
        WHERE endpoint = ? AND change_seq > ?
        ORDER BY change_seq, id
        """,
        [endpoint, since],
    )
    while True:
        rows = cursor.fetchmany(BATCH_SIZE)
        if not rows:
            return
        yield rows


def _elements(item: RegisteredField, value: Any) -> list[tuple[str | None, Any]]:
    """Normalize list and map values into ordered (key, value) pairs."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [(str(key), element) for key, element in sorted(value.items(), key=_key_order)]
    if isinstance(value, list):
        keyed = item.kind == "map"
        return [(str(index) if keyed else None, element) for index, element in enumerate(value)]
    return [(None if item.kind == "list" else "", value)]


def _key_order(pair: tuple[Any, Any]) -> tuple[int, int, str]:
    key = str(pair[0])
    return (0, int(key), "") if key.isdigit() else (1, 0, key)


def _coerce(item: RegisteredField, value: Any) -> Any:
    if value is None:
        return None
    kind = item.value_type
    if kind == "json":
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    if kind == "text":
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    if isinstance(value, dict | list):
        raise _Widen(item.field, "text")
    if kind == "boolean":
        if isinstance(value, bool):
            return 1 if value else 0
        raise _Widen(item.field, "text")
    if kind == "integer":
        if isinstance(value, bool):
            raise _Widen(item.field, "text")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            raise _Widen(item.field, "real")
        raise _Widen(item.field, "text")
    if kind == "real":
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        raise _Widen(item.field, "text")
    if kind == "datetime":
        if isinstance(value, str) and _is_datetime(value):
            return value
        raise _Widen(item.field, "text")
    raise ValueError(f"Unknown value type {kind!r}")  # pragma: no cover


def _is_datetime(value: str) -> bool:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _widest(current: str, incoming: str) -> str:
    if current == incoming:
        return current
    if {current, incoming} == {"integer", "real"}:
        return "real"
    if "json" in (current, incoming):
        return "json"
    return "text"


def _unique(name: str, used: set[str]) -> str:
    candidate = name
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{name}__{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate
