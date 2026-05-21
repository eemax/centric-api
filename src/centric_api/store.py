from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db_schema import ensure_dashboard_views, ensure_feature_tables
from .schema import DeleteCondition, EndpointSchema

PRIMARY_KEY_FIELD = "id"
MODIFIED_AT_FIELD = "_modified_at"
HARD_DELETE_TYPE_FIELD = "_centric_api_delete_type"
HARD_DELETE_DELETED_AT_FIELD = "_centric_api_deleted_at"
HARD_DELETE_SOURCE_RUN_ID_FIELD = "_centric_api_source_run_id"
HARD_DELETE_SOURCE_FILE_FIELD = "_centric_api_source_file"
DELETE_TYPE_TOMBSTONE = "tombstone"
DELETE_TYPE_HARD_DELETE = "hard_delete"


@dataclass(frozen=True)
class RawFile:
    path: Path
    endpoint: str
    is_delta: bool
    source_run_id: str
    run_mode: str | None = None
    manifest_path: Path | None = None
    manifest_sha256: str | None = None


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

    with connect(db_path) as conn:
        for raw_file in raw_files:
            content_hash = _sha256(raw_file.path)
            applied_hash = _applied_hash(conn, raw_file.path)
            if applied_hash == content_hash:
                skipped_files += 1
                continue
            if applied_hash is not None and applied_hash != content_hash:
                raise ValueError(
                    f"Raw file changed after ingest: {raw_file.path}. "
                    "Raw evidence files are expected to be immutable."
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
                    file_result["records_read"],
                    file_result["invalid_records"],
                    content_hash,
                    str(raw_file.manifest_path) if raw_file.manifest_path else None,
                    raw_file.manifest_sha256,
                    raw_file.run_mode,
                    _utc_iso(),
                ],
            )
            applied_files += 1
            records_read += file_result["records_read"]
            records_upserted += file_result["records_upserted"]
            records_deleted += file_result["records_deleted"]
            records_hard_deleted += file_result["records_hard_deleted"]
            invalid_records += file_result["invalid_records"]
            endpoints[raw_file.endpoint] += file_result["records_read"]
            upserted_ids[raw_file.endpoint].update(file_result["upserted_ids"])
            deleted_ids[raw_file.endpoint].update(file_result["deleted_ids"])
            deleted_types[raw_file.endpoint].update(file_result["deleted_types"])

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


def discover_raw_files(raw_dir: Path) -> list[RawFile]:
    if not raw_dir.exists():
        return []
    files: list[RawFile] = []
    for path in raw_dir.rglob("*.jsonl"):
        if path.name.startswith("."):
            continue
        endpoint, is_delta = _endpoint_from_filename(path.name)
        if endpoint is None:
            continue
        manifest = _load_manifest(path.parent)
        source_run_id = _manifest_run_id(manifest) or (
            path.parent.name if path.parent != raw_dir else "root"
        )
        run_mode = _manifest_mode(manifest)
        manifest_path = path.parent / "manifest.json" if manifest is not None else None
        files.append(
            RawFile(
                path=path,
                endpoint=endpoint,
                is_delta=_manifest_file_is_delta(manifest, path.name, default=is_delta),
                source_run_id=source_run_id,
                run_mode=run_mode,
                manifest_path=manifest_path,
                manifest_sha256=_sha256(manifest_path) if manifest_path else None,
            )
        )
    return sorted(files, key=lambda item: (_run_sort_key(item), item.endpoint, str(item.path)))


def _apply_raw_file(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    schema: EndpointSchema,
) -> dict[str, Any]:
    winners: dict[str, tuple[dict[str, Any], int]] = {}
    records_read = 0
    invalid_records = 0
    now = _utc_iso()

    with raw_file.path.open("r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            text = raw_line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                invalid_records += 1
                _insert_warning(conn, raw_file, None, f"line {line_number}: invalid JSON ({exc})")
                continue
            if not isinstance(payload, dict):
                invalid_records += 1
                _insert_warning(conn, raw_file, None, f"line {line_number}: record is not object")
                continue
            record_id = payload.get(PRIMARY_KEY_FIELD)
            if record_id is None or str(record_id).strip() == "":
                invalid_records += 1
                _insert_warning(conn, raw_file, None, f"line {line_number}: missing id")
                continue
            records_read += 1
            record_key = str(record_id)
            existing = winners.get(record_key)
            if existing is None or _record_is_newer(payload, line_number, existing[0], existing[1]):
                winners[record_key] = (payload, line_number)

    upserted_ids: set[str] = set()
    deleted_ids: set[str] = set()
    deleted_types: dict[str, str] = {}
    retained_ids: set[str] = set()
    for record_id, (payload, _) in sorted(winners.items()):
        payload_json = _canonical_json(payload)
        payload_hash = _hash_text(payload_json)
        modified_at = _string_value(payload.get(MODIFIED_AT_FIELD))
        is_delete = _matches_delete(payload, schema.delete_when_any)
        existing = conn.execute(
            """
            SELECT payload_json, payload_sha256, modified_at
            FROM endpoint_records
            WHERE endpoint = ? AND record_id = ?
            """,
            [raw_file.endpoint, record_id],
        ).fetchone()

        if is_delete:
            if existing is not None and _incoming_can_replace(modified_at, existing["modified_at"]):
                conn.execute(
                    "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
                    [raw_file.endpoint, record_id],
                )
                _upsert_tombstone(
                    conn,
                    raw_file=raw_file,
                    record_id=record_id,
                    payload_json=payload_json,
                    payload_hash=payload_hash,
                    modified_at=modified_at,
                    ingested_at=now,
                )
                deleted_ids.add(record_id)
                deleted_types[record_id] = DELETE_TYPE_TOMBSTONE
            elif existing is None:
                _upsert_tombstone(
                    conn,
                    raw_file=raw_file,
                    record_id=record_id,
                    payload_json=payload_json,
                    payload_hash=payload_hash,
                    modified_at=modified_at,
                    ingested_at=now,
                )
            continue

        retained_ids.add(record_id)

        if existing is None:
            _upsert_current_record(
                conn,
                raw_file=raw_file,
                record_id=record_id,
                payload_json=payload_json,
                payload_hash=payload_hash,
                modified_at=modified_at,
                ingested_at=now,
            )
            upserted_ids.add(record_id)
            continue

        if not _incoming_can_replace(modified_at, existing["modified_at"]):
            continue
        if existing["payload_sha256"] == payload_hash:
            continue
        if modified_at is not None and modified_at == existing["modified_at"]:
            _insert_warning(
                conn,
                raw_file,
                record_id,
                "same _modified_at with different payload; replaced by later raw file order",
            )
        _upsert_current_record(
            conn,
            raw_file=raw_file,
            record_id=record_id,
            payload_json=payload_json,
            payload_hash=payload_hash,
            modified_at=modified_at,
            ingested_at=now,
        )
        upserted_ids.add(record_id)

    hard_deleted_ids = _reconcile_full_snapshot_hard_deletes(
        conn,
        raw_file=raw_file,
        retained_ids=retained_ids,
        invalid_records=invalid_records,
        ingested_at=now,
    )
    deleted_ids.update(hard_deleted_ids)
    for record_id in hard_deleted_ids:
        deleted_types[record_id] = DELETE_TYPE_HARD_DELETE

    return {
        "records_read": records_read,
        "records_upserted": len(upserted_ids),
        "records_deleted": len(deleted_ids) - len(hard_deleted_ids),
        "records_hard_deleted": len(hard_deleted_ids),
        "invalid_records": invalid_records,
        "upserted_ids": upserted_ids,
        "deleted_ids": deleted_ids,
        "deleted_types": deleted_types,
    }


def _reconcile_full_snapshot_hard_deletes(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    retained_ids: set[str],
    invalid_records: int,
    ingested_at: str,
) -> set[str]:
    if raw_file.run_mode != "full" or raw_file.is_delta or invalid_records:
        return set()

    rows = conn.execute(
        """
        SELECT record_id
        FROM endpoint_records
        WHERE endpoint = ?
        """,
        [raw_file.endpoint],
    ).fetchall()
    current_ids = {str(row["record_id"]) for row in rows}
    hard_deleted_ids = current_ids - retained_ids
    for record_id in sorted(hard_deleted_ids):
        payload = {
            PRIMARY_KEY_FIELD: record_id,
            HARD_DELETE_TYPE_FIELD: "hard_delete",
            HARD_DELETE_DELETED_AT_FIELD: ingested_at,
            HARD_DELETE_SOURCE_RUN_ID_FIELD: raw_file.source_run_id,
            HARD_DELETE_SOURCE_FILE_FIELD: str(raw_file.path),
        }
        payload_json = _canonical_json(payload)
        payload_hash = _hash_text(payload_json)
        conn.execute(
            "DELETE FROM endpoint_records WHERE endpoint = ? AND record_id = ?",
            [raw_file.endpoint, record_id],
        )
        _upsert_tombstone(
            conn,
            raw_file=raw_file,
            record_id=record_id,
            payload_json=payload_json,
            payload_hash=payload_hash,
            modified_at=None,
            ingested_at=ingested_at,
        )
    return hard_deleted_ids


def _record_is_newer(
    candidate: dict[str, Any],
    candidate_line: int,
    existing: dict[str, Any],
    existing_line: int,
) -> bool:
    candidate_modified = _string_value(candidate.get(MODIFIED_AT_FIELD))
    existing_modified = _string_value(existing.get(MODIFIED_AT_FIELD))
    cmp = _compare_modified(candidate_modified, existing_modified)
    if cmp != 0:
        return cmp > 0
    return candidate_line > existing_line


def _incoming_can_replace(incoming_modified: str | None, existing_modified: str | None) -> bool:
    if incoming_modified is None:
        return existing_modified is None
    if existing_modified is None:
        return True
    return _compare_modified(incoming_modified, existing_modified) >= 0


def _compare_modified(left: str | None, right: str | None) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is not None and right_dt is not None:
        return (left_dt > right_dt) - (left_dt < right_dt)
    return (left > right) - (left < right)


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _matches_delete(payload: dict[str, Any], conditions: tuple[DeleteCondition, ...]) -> bool:
    return any(
        _extract_path(payload, condition.field) == condition.equals for condition in conditions
    )


def _extract_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _upsert_current_record(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    record_id: str,
    payload_json: str,
    payload_hash: str,
    modified_at: str | None,
    ingested_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint, record_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            payload_sha256 = excluded.payload_sha256,
            modified_at = excluded.modified_at,
            source_file = excluded.source_file,
            source_run_id = excluded.source_run_id,
            ingested_at = excluded.ingested_at
        """,
        [
            raw_file.endpoint,
            record_id,
            payload_json,
            payload_hash,
            modified_at,
            str(raw_file.path),
            raw_file.source_run_id,
            ingested_at,
        ],
    )
    conn.execute(
        "DELETE FROM endpoint_tombstones WHERE endpoint = ? AND record_id = ?",
        [raw_file.endpoint, record_id],
    )


def _upsert_tombstone(
    conn: sqlite3.Connection,
    *,
    raw_file: RawFile,
    record_id: str,
    payload_json: str,
    payload_hash: str,
    modified_at: str | None,
    ingested_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO endpoint_tombstones (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint, record_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            payload_sha256 = excluded.payload_sha256,
            modified_at = excluded.modified_at,
            source_file = excluded.source_file,
            source_run_id = excluded.source_run_id,
            ingested_at = excluded.ingested_at
        """,
        [
            raw_file.endpoint,
            record_id,
            payload_json,
            payload_hash,
            modified_at,
            str(raw_file.path),
            raw_file.source_run_id,
            ingested_at,
        ],
    )


def _insert_warning(
    conn: sqlite3.Connection,
    raw_file: RawFile,
    record_id: str | None,
    warning: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_warnings (endpoint, record_id, source_file, warning, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [raw_file.endpoint, record_id, str(raw_file.path), warning, _utc_iso()],
    )


def _applied_hash(conn: sqlite3.Connection, path: Path) -> str | None:
    row = conn.execute(
        "SELECT content_sha256 FROM applied_raw_files WHERE file_path = ?",
        [str(path)],
    ).fetchone()
    return str(row[0]) if row else None


def _endpoint_from_filename(filename: str) -> tuple[str | None, bool]:
    if filename.endswith(".delta.jsonl"):
        return filename[: -len(".delta.jsonl")], True
    if filename.endswith(".jsonl"):
        return filename[: -len(".jsonl")], False
    return None, False


def _load_manifest(directory: Path) -> dict[str, Any] | None:
    path = directory / "manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _manifest_run_id(manifest: dict[str, Any] | None) -> str | None:
    value = manifest.get("run_id") if manifest else None
    return str(value) if value else None


def _manifest_mode(manifest: dict[str, Any] | None) -> str | None:
    value = manifest.get("mode") if manifest else None
    return str(value) if value else None


def _manifest_file_is_delta(
    manifest: dict[str, Any] | None,
    filename: str,
    *,
    default: bool,
) -> bool:
    if manifest is None:
        return default
    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, dict):
        return default
    for endpoint in endpoints.values():
        if not isinstance(endpoint, dict) or endpoint.get("file") != filename:
            continue
        is_delta = endpoint.get("is_delta")
        return bool(is_delta) if isinstance(is_delta, bool) else default
    return default


def _run_sort_key(raw_file: RawFile) -> tuple[int, str]:
    if raw_file.manifest_path and raw_file.manifest_path.is_file():
        manifest = _load_manifest(raw_file.manifest_path.parent)
        started_at = manifest.get("started_at") if manifest else None
        if isinstance(started_at, str):
            return (0, started_at)
    return (1, str(raw_file.path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
