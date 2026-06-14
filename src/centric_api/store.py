from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .db_schema import ensure_dashboard_views, ensure_endpoint_state_table, ensure_feature_tables
from .schema import EndpointSchema
from .store_discovery import RawFile, _sha256, discover_raw_files
from .store_ingest import (
    DELETE_TYPE_HARD_DELETE,
    DELETE_TYPE_TOMBSTONE,
    HARD_DELETE_DELETED_AT_FIELD,
    HARD_DELETE_SOURCE_FILE_FIELD,
    HARD_DELETE_SOURCE_RUN_ID_FIELD,
    HARD_DELETE_TYPE_FIELD,
    MODIFIED_AT_FIELD,
    PRIMARY_KEY_FIELD,
    _apply_raw_file,
    _utc_iso,
)

# Keep repr/pickle/introspection compatible with the original public facade.
RawFile.__module__ = __name__

__all__ = [
    "DELETE_TYPE_HARD_DELETE",
    "DELETE_TYPE_TOMBSTONE",
    "HARD_DELETE_DELETED_AT_FIELD",
    "HARD_DELETE_SOURCE_FILE_FIELD",
    "HARD_DELETE_SOURCE_RUN_ID_FIELD",
    "HARD_DELETE_TYPE_FIELD",
    "IngestResult",
    "MODIFIED_AT_FIELD",
    "PRIMARY_KEY_FIELD",
    "RawFile",
    "connect",
    "connect_readonly",
    "discover_raw_files",
    "endpoint_has_cache_evidence",
    "initialize_store",
    "ingest_raw_dir",
    "refresh_endpoint_state",
    "table_exists",
]


@dataclass(frozen=True)
class IngestResult:
    applied_files: int
    skipped_files: int
    records_read: int
    records_upserted: int
    records_deleted: int
    records_hard_deleted: int
    invalid_records: int
    endpoints: dict[str, int]
    upserted_record_ids_by_endpoint: dict[str, tuple[str, ...]]
    deleted_record_ids_by_endpoint: dict[str, tuple[str, ...]]
    deleted_record_delete_types_by_endpoint: dict[str, dict[str, str]]

    @property
    def changed_record_ids_by_endpoint(self) -> dict[str, tuple[str, ...]]:
        merged: dict[str, tuple[str, ...]] = {}
        for endpoint in sorted(
            set(self.upserted_record_ids_by_endpoint) | set(self.deleted_record_ids_by_endpoint)
        ):
            merged[endpoint] = tuple(
                sorted(
                    set(self.upserted_record_ids_by_endpoint.get(endpoint, ()))
                    | set(self.deleted_record_ids_by_endpoint.get(endpoint, ()))
                )
            )
        return merged


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    initialize_store(conn)
    ensure_feature_tables(conn)
    ensure_dashboard_views(conn)
    return conn


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        [name],
    ).fetchone()
    return row is not None


def endpoint_has_cache_evidence(conn: sqlite3.Connection, endpoint: str) -> bool:
    if table_exists(conn, "applied_raw_files"):
        row = conn.execute(
            """
            SELECT 1
            FROM applied_raw_files
            WHERE endpoint = ?
            LIMIT 1
            """,
            [endpoint],
        ).fetchone()
        if row is not None:
            return True
    for table in ("endpoint_records", "endpoint_tombstones"):
        if not table_exists(conn, table):
            continue
        row = conn.execute(
            f"""
            SELECT 1
            FROM {table}
            WHERE endpoint = ?
            LIMIT 1
            """,
            [endpoint],
        ).fetchone()
        if row is not None:
            return True
    return False


def initialize_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS applied_raw_files (
            file_path TEXT PRIMARY KEY,
            endpoint TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            is_delta INTEGER NOT NULL,
            record_count INTEGER NOT NULL,
            invalid_record_count INTEGER NOT NULL DEFAULT 0,
            content_sha256 TEXT NOT NULL,
            manifest_path TEXT,
            manifest_sha256 TEXT,
            run_mode TEXT,
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endpoint_records (
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            modified_at TEXT,
            source_file TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            PRIMARY KEY (endpoint, record_id)
        );

        CREATE TABLE IF NOT EXISTS endpoint_tombstones (
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            modified_at TEXT,
            source_file TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            PRIMARY KEY (endpoint, record_id)
        );

        CREATE TABLE IF NOT EXISTS ingest_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            record_id TEXT,
            source_file TEXT NOT NULL,
            warning TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE VIEW IF NOT EXISTS current_endpoint_records AS
        SELECT
            endpoint, record_id, payload_json, modified_at, source_file,
            source_run_id, ingested_at
        FROM endpoint_records;
        """
    )


def ingest_raw_dir(
    raw_dir: Path,
    db_path: Path,
    *,
    schemas: dict[str, EndpointSchema],
) -> IngestResult:
    raw_files = discover_raw_files(raw_dir)
    applied_files = 0
    skipped_files = 0
    records_read = 0
    records_upserted = 0
    records_deleted = 0
    records_hard_deleted = 0
    invalid_records = 0
    endpoints: defaultdict[str, int] = defaultdict(int)
    upserted_ids: defaultdict[str, set[str]] = defaultdict(set)
    deleted_ids: defaultdict[str, set[str]] = defaultdict(set)
    deleted_types: defaultdict[str, dict[str, str]] = defaultdict(dict)
    touched_endpoints: set[str] = set()

    with connect(db_path) as conn:
        state_was_empty = _endpoint_state_is_empty(conn)
        for raw_file in raw_files:
            content_hash = _sha256(raw_file.path)
            applied_hash, applied_manifest_hash = _applied_hashes(conn, raw_file.path)
            if applied_hash == content_hash and applied_manifest_hash == raw_file.manifest_sha256:
                skipped_files += 1
                continue
            if applied_hash is not None and applied_hash != content_hash:
                raise ValueError(
                    f"Raw file changed after ingest: {raw_file.path}. "
                    "Raw evidence files are expected to be immutable."
                )
            if applied_hash == content_hash and applied_manifest_hash != raw_file.manifest_sha256:
                raise ValueError(
                    f"Raw manifest changed after ingest: {raw_file.manifest_path}. "
                    "Raw evidence manifests are expected to be immutable."
                )

            schema = schemas.get(raw_file.endpoint, EndpointSchema(name=raw_file.endpoint))
            file_result = _apply_raw_file(conn, raw_file=raw_file, schema=schema)
            conn.execute(
                """
                INSERT INTO applied_raw_files (
                    file_path, endpoint, source_run_id, is_delta, record_count,
                    invalid_record_count, content_sha256, manifest_path, manifest_sha256,
                    run_mode, ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(raw_file.path),
                    raw_file.endpoint,
                    raw_file.source_run_id,
                    int(raw_file.is_delta),
                    file_result.records_read,
                    file_result.invalid_records,
                    content_hash,
                    str(raw_file.manifest_path) if raw_file.manifest_path else None,
                    raw_file.manifest_sha256,
                    raw_file.run_mode,
                    _utc_iso(),
                ],
            )
            applied_files += 1
            records_read += file_result.records_read
            records_upserted += file_result.records_upserted
            records_deleted += file_result.records_deleted
            records_hard_deleted += file_result.records_hard_deleted
            invalid_records += file_result.invalid_records
            endpoints[raw_file.endpoint] += file_result.records_read
            upserted_ids[raw_file.endpoint].update(file_result.upserted_ids)
            deleted_ids[raw_file.endpoint].update(file_result.deleted_ids)
            deleted_types[raw_file.endpoint].update(file_result.deleted_types)
            touched_endpoints.add(raw_file.endpoint)

        if touched_endpoints and state_was_empty:
            refresh_endpoint_state(conn)
        elif touched_endpoints:
            refresh_endpoint_state(conn, touched_endpoints)
        elif raw_files and state_was_empty:
            refresh_endpoint_state(conn)

    return IngestResult(
        applied_files=applied_files,
        skipped_files=skipped_files,
        records_read=records_read,
        records_upserted=records_upserted,
        records_deleted=records_deleted,
        records_hard_deleted=records_hard_deleted,
        invalid_records=invalid_records,
        endpoints=dict(sorted(endpoints.items())),
        upserted_record_ids_by_endpoint={
            endpoint: tuple(sorted(record_ids))
            for endpoint, record_ids in sorted(upserted_ids.items())
            if record_ids
        },
        deleted_record_ids_by_endpoint={
            endpoint: tuple(sorted(record_ids))
            for endpoint, record_ids in sorted(deleted_ids.items())
            if record_ids
        },
        deleted_record_delete_types_by_endpoint={
            endpoint: dict(sorted(record_types.items()))
            for endpoint, record_types in sorted(deleted_types.items())
            if record_types
        },
    )


def refresh_endpoint_state(
    conn: sqlite3.Connection,
    endpoints: Iterable[str] | None = None,
) -> None:
    ensure_endpoint_state_table(conn)
    now = _utc_iso()
    if endpoints is None:
        conn.execute("DELETE FROM endpoint_state")
        conn.execute(
            """
            INSERT INTO endpoint_state (
                endpoint, current_count, tombstone_count, latest_modified_at,
                latest_ingested_at, updated_at
            )
            SELECT
                endpoint,
                SUM(current_count) AS current_count,
                SUM(tombstone_count) AS tombstone_count,
                MAX(latest_modified_at) AS latest_modified_at,
                MAX(latest_ingested_at) AS latest_ingested_at,
                ? AS updated_at
            FROM (
                SELECT
                    endpoint,
                    COUNT(*) AS current_count,
                    0 AS tombstone_count,
                    MAX(modified_at) AS latest_modified_at,
                    MAX(ingested_at) AS latest_ingested_at
                FROM endpoint_records
                GROUP BY endpoint
                UNION ALL
                SELECT
                    endpoint,
                    0 AS current_count,
                    COUNT(*) AS tombstone_count,
                    MAX(modified_at) AS latest_modified_at,
                    MAX(ingested_at) AS latest_ingested_at
                FROM endpoint_tombstones
                GROUP BY endpoint
            )
            GROUP BY endpoint
            """,
            [now],
        )
        return

    for endpoint in sorted({str(endpoint) for endpoint in endpoints}):
        row = conn.execute(
            """
            SELECT
                SUM(current_count) AS current_count,
                SUM(tombstone_count) AS tombstone_count,
                MAX(latest_modified_at) AS latest_modified_at,
                MAX(latest_ingested_at) AS latest_ingested_at
            FROM (
                SELECT
                    COUNT(*) AS current_count,
                    0 AS tombstone_count,
                    MAX(modified_at) AS latest_modified_at,
                    MAX(ingested_at) AS latest_ingested_at
                FROM endpoint_records
                WHERE endpoint = ?
                UNION ALL
                SELECT
                    0 AS current_count,
                    COUNT(*) AS tombstone_count,
                    MAX(modified_at) AS latest_modified_at,
                    MAX(ingested_at) AS latest_ingested_at
                FROM endpoint_tombstones
                WHERE endpoint = ?
            )
            """,
            [endpoint, endpoint],
        ).fetchone()
        current_count = int(row["current_count"] or 0) if row is not None else 0
        tombstone_count = int(row["tombstone_count"] or 0) if row is not None else 0
        if current_count == 0 and tombstone_count == 0:
            conn.execute("DELETE FROM endpoint_state WHERE endpoint = ?", [endpoint])
            continue
        conn.execute(
            """
            INSERT INTO endpoint_state (
                endpoint, current_count, tombstone_count, latest_modified_at,
                latest_ingested_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                current_count = excluded.current_count,
                tombstone_count = excluded.tombstone_count,
                latest_modified_at = excluded.latest_modified_at,
                latest_ingested_at = excluded.latest_ingested_at,
                updated_at = excluded.updated_at
            """,
            [
                endpoint,
                current_count,
                tombstone_count,
                row["latest_modified_at"] if row is not None else None,
                row["latest_ingested_at"] if row is not None else None,
                now,
            ],
        )


def _endpoint_state_is_empty(conn: sqlite3.Connection) -> bool:
    ensure_endpoint_state_table(conn)
    row = conn.execute("SELECT 1 FROM endpoint_state LIMIT 1").fetchone()
    return row is None


def _applied_hashes(conn: sqlite3.Connection, path: Path) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT content_sha256, manifest_sha256 FROM applied_raw_files WHERE file_path = ?",
        [str(path)],
    ).fetchone()
    if row is None:
        return None, None
    content_sha = str(row["content_sha256"])
    manifest_sha = row["manifest_sha256"]
    return content_sha, str(manifest_sha) if manifest_sha is not None else None
