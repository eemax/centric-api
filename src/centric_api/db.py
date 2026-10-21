from __future__ import annotations

import os
import socket
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = "1"
LOCK_STALE_AFTER = timedelta(minutes=15)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_swagger (
    api_version TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    spec TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_runs (
    run_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS meta_run_endpoints (
    run_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    mode TEXT NOT NULL,
    delta_floor TEXT,
    resumed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    expected INTEGER,
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    restored INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    passes INTEGER NOT NULL DEFAULT 1,
    complete INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    warning TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY (run_id, endpoint)
);

CREATE TABLE IF NOT EXISTS meta_endpoint_state (
    endpoint TEXT PRIMARY KEY,
    last_success_started_at TEXT,
    last_success_finished_at TEXT,
    last_success_mode TEXT,
    last_full_success_at TEXT,
    last_attempt_started_at TEXT,
    last_attempt_status TEXT,
    last_attempt_error TEXT,
    pending_run_id TEXT,
    pending_mode TEXT,
    pending_pass INTEGER,
    pending_started_at TEXT,
    pending_delta_floor TEXT,
    pending_next_skip INTEGER,
    pending_fetched INTEGER
);

CREATE TABLE IF NOT EXISTS bronze_records (
    endpoint TEXT NOT NULL,
    id TEXT NOT NULL,
    modified_at TEXT,
    payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_seen_run TEXT NOT NULL,
    change_seq INTEGER NOT NULL,
    PRIMARY KEY (endpoint, id)
);
CREATE INDEX IF NOT EXISTS bronze_records_seen ON bronze_records (endpoint, last_seen_run);
CREATE INDEX IF NOT EXISTS bronze_records_changes ON bronze_records (endpoint, change_seq);

CREATE TABLE IF NOT EXISTS meta_silver_fields (
    endpoint TEXT NOT NULL,
    field TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value_type TEXT NOT NULL,
    spec_type TEXT,
    object_type TEXT,
    table_name TEXT NOT NULL,
    column_name TEXT,
    in_spec INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (endpoint, field)
);

CREATE TABLE IF NOT EXISTS meta_silver_state (
    endpoint TEXT PRIMARY KEY,
    synced_seq INTEGER NOT NULL,
    plan_sha256 TEXT NOT NULL,
    rebuilt_at TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_unmapped_fields (
    endpoint TEXT NOT NULL,
    field TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (endpoint, field)
);
"""


class LockError(RuntimeError):
    pass


class DatabaseError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the database, creating the schema on first use.

    Read-only connections never write, never block on a running fetch and fail if the
    database does not exist yet. Refuses SQLite files that hold other applications' tables.
    """
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        _check_schema(conn, path)
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    if not _check_schema(conn, path):
        conn.execute("PRAGMA journal_mode=WAL")
        # executescript() commits any open transaction first, so it runs as its own unit.
        conn.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\nCOMMIT;")
        with transaction(conn):
            if not table_has_row(conn, "meta_info", "schema_version"):
                conn.executemany(
                    "INSERT INTO meta_info (key, value) VALUES (?, ?)",
                    [("schema_version", SCHEMA_VERSION), ("change_seq", "0")],
                )
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _check_schema(conn: sqlite3.Connection, path: Path) -> bool:
    """Return True when the schema exists; raise for foreign or incompatible databases."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "meta_info" not in tables:
        if tables:
            raise DatabaseError(
                f"{path} already contains other tables and is not a centric-api database; "
                "choose a different database path."
            )
        return False
    row = conn.execute("SELECT value FROM meta_info WHERE key = 'schema_version'").fetchone()
    if row is None:
        return False
    if row[0] != SCHEMA_VERSION:
        raise DatabaseError(
            f"{path} uses database schema version {row[0]}; this version of centric-api "
            f"supports version {SCHEMA_VERSION}."
        )
    return True


def stored_endpoints(conn: sqlite3.Connection) -> list[str]:
    """Endpoints that have records, sorted. Hops along the primary key index from one
    endpoint name to the next (a few lookups) instead of scanning every record."""
    rows = conn.execute(
        """
        WITH RECURSIVE names(name) AS (
            SELECT MIN(endpoint) FROM bronze_records
            UNION ALL
            SELECT (SELECT MIN(endpoint) FROM bronze_records WHERE endpoint > names.name)
            FROM names WHERE names.name IS NOT NULL
        )
        SELECT name FROM names WHERE name IS NOT NULL
        """
    ).fetchall()
    return [row[0] for row in rows]


def latest_change_seq(conn: sqlite3.Connection, endpoints: list[str] | None = None) -> int:
    """The highest change_seq stamped on any record (of these endpoints), 0 if none.

    Records get a new stamp only when they really change, so this moves only on real
    changes (the meta_info counter also advances for pages that changed nothing).
    One index lookup per endpoint.
    """
    names = stored_endpoints(conn) if endpoints is None else endpoints
    best = 0
    for name in names:
        row = conn.execute(
            "SELECT MAX(change_seq) FROM bronze_records WHERE endpoint = ?", [name]
        ).fetchone()
        best = max(best, int(row[0] or 0))
    return best


def table_has_row(conn: sqlite3.Connection, table: str, key: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {quote(table)} WHERE key = ?", [key]).fetchone() is not None


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def next_change_seq(conn: sqlite3.Connection) -> int:
    """Allocate the next change sequence number. Call inside a transaction."""
    row = conn.execute("SELECT value FROM meta_info WHERE key = 'change_seq'").fetchone()
    value = int(row["value"]) + 1
    conn.execute("UPDATE meta_info SET value = ? WHERE key = 'change_seq'", [str(value)])
    return value


def _owner_is_dead_local_process(owner: str) -> bool:
    """True only when the lock owner ran on this host and its process no longer exists.

    Owners look like ``host:pid:nonce``. A lock held from another host, or by a process
    that still exists (even one that reused the pid), is never treated as dead here: the
    heartbeat timeout covers those, so a live lock is never taken over early.
    """
    host, _, rest = owner.partition(":")
    pid_text = rest.partition(":")[0]
    if host != socket.gethostname() or not pid_text.isdigit():
        return False
    pid = int(pid_text)
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


class RunLock:
    """A single-writer lock stored in the database, refreshed by heartbeats.

    A lock whose heartbeat is older than LOCK_STALE_AFTER is treated as abandoned, and so
    is one whose owning process on this host is gone (crashed or killed with SIGKILL),
    without waiting for the heartbeat to age.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def acquire(self) -> None:
        now = utc_now()
        with transaction(self.conn):
            row = self.conn.execute("SELECT owner, heartbeat_at FROM meta_lock").fetchone()
            if row is not None:
                heartbeat = datetime.fromisoformat(row["heartbeat_at"].replace("Z", "+00:00"))
                fresh = now - heartbeat < LOCK_STALE_AFTER
                if fresh and not _owner_is_dead_local_process(row["owner"]):
                    raise LockError(
                        f"Another centric-api run holds the database lock ({row['owner']}, "
                        f"last heartbeat {row['heartbeat_at']})."
                    )
                self.conn.execute("DELETE FROM meta_lock")
            self.conn.execute(
                "INSERT INTO meta_lock (id, owner, acquired_at, heartbeat_at) VALUES (1, ?, ?, ?)",
                [self.owner, iso(now), iso(now)],
            )

    def heartbeat(self) -> None:
        """Refresh the heartbeat. Call inside a transaction."""
        cursor = self.conn.execute(
            "UPDATE meta_lock SET heartbeat_at = ? WHERE id = 1 AND owner = ?",
            [iso(utc_now()), self.owner],
        )
        if cursor.rowcount != 1:
            raise LockError("The database lock was taken over by another run.")

    def release(self) -> None:
        with transaction(self.conn):
            self.conn.execute("DELETE FROM meta_lock WHERE id = 1 AND owner = ?", [self.owner])

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", [name]
    ).fetchone()
    return row is not None
