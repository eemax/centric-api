from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import ConfigError
from .download_config import DownloadFilter, DownloadJob, DownloadLookupFilter

DOCUMENT_ENDPOINT = "documents"
DOCUMENT_REVISION_ENDPOINT = "document_revisions"
DOCUMENT_REFERENCE_PATHS = ("documents", "referenced_documents")
LATEST_REVISION_FIELD = "latest_revision"
DOCUMENT_NAME_FIELD = "node_name"
REVISION_FILENAME_FIELD = "file_name"

DownloadLogCallback = Callable[[dict[str, Any]], None] | None


@dataclass(frozen=True)
class CandidateDocument:
    document_id: str
    source_endpoint: str
    source_record_id: str
    source_path: str


@dataclass(frozen=True)
class ResolvedDocument:
    document_id: str
    document_payload: dict[str, Any]
    revision_payload: dict[str, Any] | None
    latest_revision_id: str
    filename: str
    candidates: tuple[CandidateDocument, ...]


def preflight_download_cache(conn: sqlite3.Connection, job: DownloadJob) -> None:
    required_endpoints = {source.endpoint for source in job.sources}
    required_endpoints.add(DOCUMENT_ENDPOINT)
    required_endpoints.add(DOCUMENT_REVISION_ENDPOINT)
    required_endpoints.update(_lookup_filter_endpoints(job))
    missing = [
        endpoint
        for endpoint in sorted(required_endpoints)
        if not _endpoint_has_cache_evidence(conn, endpoint)
    ]
    if missing:
        raise ConfigError(
            f"Download job {job.name!r} requires cached endpoint records for: "
            f"{', '.join(missing)}. Run centric-api fetch for those endpoints first."
        )


def collect_candidate_documents(
    conn: sqlite3.Connection,
    job: DownloadJob,
    *,
    log_callback: DownloadLogCallback,
) -> dict[str, list[CandidateDocument]]:
    candidates: dict[str, list[CandidateDocument]] = {}
    for source in job.sources:
        rows = conn.execute(
            """
            SELECT record_id, payload_json
            FROM endpoint_records
            WHERE endpoint = ?
            ORDER BY record_id
            """,
            [source.endpoint],
        ).fetchall()
        for row in rows:
            payload = _json_dict(row["payload_json"])
            if not _matches_filters(conn, payload, source.filters):
                _emit(
                    log_callback,
                    {
                        "level": "debug",
                        "event": "download_source_filtered",
                        "endpoint": source.endpoint,
                        "record_id": row["record_id"],
                    },
                )
                continue
            if source.endpoint == DOCUMENT_ENDPOINT:
                candidates.setdefault(str(row["record_id"]), []).append(
                    CandidateDocument(
                        document_id=str(row["record_id"]),
                        source_endpoint=source.endpoint,
                        source_record_id=str(row["record_id"]),
                        source_path="$self",
                    )
                )
            else:
                for path in DOCUMENT_REFERENCE_PATHS:
                    for document_id in sorted(
                        set(_document_ids_from_value(extract_path(payload, path)))
                    ):
                        candidates.setdefault(document_id, []).append(
                            CandidateDocument(
                                document_id=document_id,
                                source_endpoint=source.endpoint,
                                source_record_id=str(row["record_id"]),
                                source_path=path,
                            )
                        )
    return candidates


def resolve_documents(
    conn: sqlite3.Connection,
    job: DownloadJob,
    candidates: dict[str, list[CandidateDocument]],
    *,
    log_callback: DownloadLogCallback,
) -> list[ResolvedDocument]:
    documents: list[ResolvedDocument] = []
    for document_id, refs in sorted(candidates.items()):
        row = conn.execute(
            """
            SELECT payload_json
            FROM endpoint_records
            WHERE endpoint = ? AND record_id = ?
            """,
            [DOCUMENT_ENDPOINT, document_id],
        ).fetchone()
        if row is None:
            _emit(
                log_callback,
                {
                    "level": "summary",
                    "event": "download_document_missing",
                    "document_id": document_id,
                },
            )
            continue
        payload = _json_dict(row["payload_json"])
        if not _matches_filters(conn, payload, job.document_filters):
            _emit(
                log_callback,
                {
                    "level": "debug",
                    "event": "download_document_filtered",
                    "document_id": document_id,
                },
            )
            continue
        revision_id = string_value(extract_path(payload, LATEST_REVISION_FIELD))
        if not revision_id:
            _emit(
                log_callback,
                {
                    "level": "summary",
                    "event": "download_revision_missing",
                    "document_id": document_id,
                },
            )
            continue
        revision_payload = _load_revision_payload(
            conn,
            revision_id=revision_id,
            document_id=document_id,
            log_callback=log_callback,
        )
        if revision_payload is None:
            continue
        if not _matches_filters(
            conn,
            revision_payload,
            job.revision_filters,
        ):
            _emit(
                log_callback,
                {
                    "level": "debug",
                    "event": "download_revision_filtered",
                    "document_id": document_id,
                    "revision_id": revision_id,
                },
            )
            continue
        filename = (
            string_value(extract_path(revision_payload, REVISION_FILENAME_FIELD))
            or string_value(extract_path(payload, DOCUMENT_NAME_FIELD))
            or f"{document_id}.bin"
        )
        documents.append(
            ResolvedDocument(
                document_id=document_id,
                document_payload=payload,
                revision_payload=revision_payload,
                latest_revision_id=revision_id,
                filename=filename,
                candidates=tuple(refs),
            )
        )
    return documents


def extract_path(payload: Any, path: str) -> Any:
    return _extract_path_with_presence(payload, path)[1]


def string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _lookup_filter_endpoints(job: DownloadJob) -> set[str]:
    endpoints: set[str] = set()
    for source in job.sources:
        endpoints.update(_lookup_endpoints_from_filters(source.filters))
    endpoints.update(_lookup_endpoints_from_filters(job.document_filters))
    endpoints.update(_lookup_endpoints_from_filters(job.revision_filters))
    return endpoints


def _lookup_endpoints_from_filters(filters: tuple[DownloadFilter, ...]) -> set[str]:
    return {item.lookup.endpoint for item in filters if item.lookup is not None}


def _endpoint_has_cache_evidence(conn: sqlite3.Connection, endpoint: str) -> bool:
    if _table_exists(conn, "applied_raw_files"):
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
    if _table_exists(conn, "endpoint_tombstones"):
        row = conn.execute(
            """
            SELECT 1
            FROM endpoint_tombstones
            WHERE endpoint = ?
            LIMIT 1
            """,
            [endpoint],
        ).fetchone()
        if row is not None:
            return True
    row = conn.execute(
        """
        SELECT 1
        FROM endpoint_records
        WHERE endpoint = ?
        LIMIT 1
        """,
        [endpoint],
    ).fetchone()
    return row is not None


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        [table_name],
    ).fetchone()
    return row is not None


def _load_revision_payload(
    conn: sqlite3.Connection,
    *,
    revision_id: str,
    document_id: str,
    log_callback: DownloadLogCallback,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT payload_json
        FROM endpoint_records
        WHERE endpoint = ? AND record_id = ?
        """,
        [DOCUMENT_REVISION_ENDPOINT, revision_id],
    ).fetchone()
    if row is None:
        _emit(
            log_callback,
            {
                "level": "summary",
                "event": "download_revision_record_missing",
                "document_id": document_id,
                "revision_id": revision_id,
            },
        )
        return None
    return _json_dict(row["payload_json"])


def _matches_filters(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    filters: tuple[DownloadFilter, ...],
) -> bool:
    return all(_matches_filter(conn, payload, item) for item in filters)


def _matches_filter(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    item: DownloadFilter,
) -> bool:
    found, value = _extract_path_with_presence(payload, item.path)
    if item.lookup is not None:
        if not found or not isinstance(value, str) or not value or value == "centric:":
            return False
        lookup_payload = _load_lookup_payload(
            conn,
            endpoint=item.lookup.endpoint,
            record_id=value,
        )
        if lookup_payload is None:
            return False
        return _matches_lookup_filter(lookup_payload, item.lookup)
    values = value if isinstance(value, list) else [value]
    if item.exists is not None:
        return found == item.exists
    if not found:
        return False
    if item.in_values is not None:
        return any(value_item in item.in_values for value_item in values)
    if item.contains is not None:
        return any(_contains(value_item, item.contains) for value_item in values)
    if item.matches is not None:
        return any(re.search(item.matches, str(value_item or "")) for value_item in values)
    return any(value_item == item.equals for value_item in values)


def _matches_lookup_filter(payload: dict[str, Any], item: DownloadLookupFilter) -> bool:
    found, value = _extract_path_with_presence(payload, item.path)
    values = value if isinstance(value, list) else [value]
    if item.exists is not None:
        return found == item.exists
    if not found:
        return False
    if item.in_values is not None:
        return any(value_item in item.in_values for value_item in values)
    if item.contains is not None:
        return any(_contains(value_item, item.contains) for value_item in values)
    if item.matches is not None:
        return any(re.search(item.matches, str(value_item or "")) for value_item in values)
    return any(value_item == item.equals for value_item in values)


def _load_lookup_payload(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    record_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT payload_json
        FROM endpoint_records
        WHERE endpoint = ? AND record_id = ?
        """,
        [endpoint, record_id],
    ).fetchone()
    return _json_dict(row["payload_json"]) if row is not None else None


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, str) and isinstance(expected, str):
        return expected in value
    if isinstance(value, list):
        return expected in value
    return value == expected


def _extract_path_with_presence(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _document_ids_from_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value and value != "centric:" else []
    if isinstance(value, list):
        ids: list[str] = []
        for item in value:
            ids.extend(_document_ids_from_value(item))
        return ids
    if isinstance(value, dict):
        ids = []
        for item in value.values():
            ids.extend(_document_ids_from_value(item))
        return ids
    return []


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}


def _emit(callback: DownloadLogCallback, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)
