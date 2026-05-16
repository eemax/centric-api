from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any, BinaryIO

import httpx
import yaml

from .auth import AuthContext
from .config import ConfigError, runtime_home, runtime_path
from .store import connect

DEFAULT_DOWNLOAD_CONFIG_PATH = Path("config/download.yml")
PRIVATE_DOWNLOAD_CONFIG_PATH = Path("download.yml")
DEFAULT_DOWNLOAD_DIR = Path("downloads")
DOCUMENT_ENDPOINT = "documents"
REVISION_DOWNLOAD_API_VERSION = "v2"

DownloadLogCallback = Callable[[dict[str, Any]], None] | None


@dataclass(frozen=True)
class DownloadFilter:
    path: str
    equals: Any = None
    in_values: tuple[Any, ...] | None = None
    contains: Any = None
    matches: str | None = None
    exists: bool | None = None


@dataclass(frozen=True)
class DownloadSource:
    endpoint: str
    filters: tuple[DownloadFilter, ...] = ()
    document_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadJob:
    name: str
    sources: tuple[DownloadSource, ...]
    document_filters: tuple[DownloadFilter, ...] = ()
    max_documents: int | None = None
    revision_field: str = "latest_revision"
    filename_field: str = "node_name"


@dataclass(frozen=True)
class DownloadConfig:
    path: Path
    jobs: tuple[DownloadJob, ...]
    output_dir: Path = field(default_factory=lambda: runtime_path(DEFAULT_DOWNLOAD_DIR))


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
    latest_revision_id: str
    filename: str
    candidates: tuple[CandidateDocument, ...]


@dataclass(frozen=True)
class DownloadRunResult:
    run_id: str
    job_name: str
    manifest_path: Path
    selected_count: int
    downloaded_count: int
    already_present_count: int
    failed_count: int
    skipped_count: int
    dry_run: bool
    items: tuple[dict[str, Any], ...]


def resolve_download_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    private_path = runtime_home() / PRIVATE_DOWNLOAD_CONFIG_PATH
    if private_path.is_file():
        return private_path
    return DEFAULT_DOWNLOAD_CONFIG_PATH


def load_download_config(path: str | Path | None = None) -> DownloadConfig:
    config_path = resolve_download_config_path(path)
    payload = _load_payload(config_path)
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("download config version must be 1.")
    output_dir = _runtime_output_dir(payload.get("output_dir"))
    jobs_raw = _list(payload.get("jobs"), "jobs")
    jobs = tuple(_parse_job(raw, index) for index, raw in enumerate(jobs_raw))
    if not jobs:
        raise ConfigError("download config must contain at least one job.")
    _ensure_unique_job_names(jobs)
    return DownloadConfig(path=config_path, jobs=jobs, output_dir=output_dir)


def run_download_job(
    *,
    db_path: Path,
    auth_ctx: AuthContext | None,
    config: DownloadConfig,
    job_name: str | None = None,
    dry_run: bool = False,
    log_callback: DownloadLogCallback = None,
) -> DownloadRunResult:
    job = _select_job(config, job_name)
    started = time.time()
    created_at = datetime.now(UTC)
    run_id = _allocate_run_id(config.output_dir, created_at, job.name)
    run_dir = config.output_dir / "runs" / run_id
    files_dir = config.output_dir / "files"

    _emit(
        log_callback,
        {
            "level": "summary",
            "event": "download_start",
            "run_id": run_id,
            "job": job.name,
            "config": str(config.path),
            "db": str(db_path),
            "dry_run": dry_run,
        },
    )

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        candidates = _collect_candidate_documents(conn, job, log_callback=log_callback)
        documents = _resolve_documents(conn, job, candidates, log_callback=log_callback)
        previous = _load_previous_downloads(conn)

    if job.max_documents is not None:
        documents = documents[: job.max_documents]

    items: list[dict[str, Any]] = []
    downloaded_count = 0
    already_present_count = 0
    failed_count = 0
    skipped_count = 0

    for document in documents:
        previous_revision = previous.get(document.document_id)
        target_path = _document_target_path(
            files_dir,
            document_id=document.document_id,
            revision_id=document.latest_revision_id,
            filename=document.filename,
        )
        base_item = {
            "document_id": document.document_id,
            "document_name": document.filename,
            "latest_revision_id": document.latest_revision_id,
            "current_revision_id": _string_value(
                _extract_path(document.document_payload, "current_revision")
            ),
            "document_modified_at": _string_value(
                _extract_path(document.document_payload, "_modified_at")
            ),
            "latest_at_run": True,
            "previous_downloaded_revision_id": previous_revision,
            "previous_was_outdated": (
                previous_revision is not None and previous_revision != document.latest_revision_id
            ),
            "source_refs": [
                {
                    "endpoint": candidate.source_endpoint,
                    "record_id": candidate.source_record_id,
                    "document_path": candidate.source_path,
                }
                for candidate in document.candidates
            ],
            "file_path": str(target_path),
        }
        if dry_run:
            item = {**base_item, "status": "dry_run"}
            skipped_count += 1
        elif target_path.is_file():
            item = {
                **base_item,
                "status": "already_present",
                "sha256": _sha256(target_path),
                "bytes": target_path.stat().st_size,
            }
            already_present_count += 1
        else:
            if auth_ctx is None:
                raise RuntimeError("auth context is required to download files.")
            try:
                downloaded = download_revision_file(
                    auth_ctx,
                    revision_id=document.latest_revision_id,
                    target_path=target_path,
                    fallback_filename=document.filename,
                    log_callback=log_callback,
                )
            except Exception as exc:
                item = {**base_item, "status": "failed", "error": str(exc)}
                failed_count += 1
            else:
                item = {
                    **base_item,
                    "status": "downloaded",
                    "file_path": str(downloaded.path),
                    "sha256": downloaded.sha256,
                    "bytes": downloaded.bytes_written,
                    "content_type": downloaded.content_type,
                }
                downloaded_count += 1

        items.append(item)
        _emit(
            log_callback,
            {
                "level": "summary",
                "event": "download_item",
                "document_id": item["document_id"],
                "revision_id": item["latest_revision_id"],
                "status": item["status"],
                "file": item["file_path"],
            },
        )

    duration_seconds = time.time() - started
    manifest = {
        "run_id": run_id,
        "job": job.name,
        "config": str(config.path),
        "db": str(db_path),
        "dry_run": dry_run,
        "started_at": _datetime_to_db(created_at),
        "finished_at": _datetime_to_db(datetime.now(UTC)),
        "duration_seconds": round(duration_seconds, 3),
        "selected_count": len(documents),
        "downloaded_count": downloaded_count,
        "already_present_count": already_present_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "items": items,
    }
    manifest_path = _write_manifest(run_dir, manifest)

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _record_download_run(conn, manifest_path=manifest_path, manifest=manifest)

    status = "failed" if failed_count and failed_count == len(documents) else "ok"
    if failed_count and failed_count < len(documents):
        status = "partial"
    _emit(
        log_callback,
        {
            "level": "summary",
            "event": f"download_{status}",
            "run_id": run_id,
            "job": job.name,
            "selected": len(documents),
            "downloaded": downloaded_count,
            "already_present": already_present_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "duration_seconds": round(duration_seconds, 3),
            "manifest": str(manifest_path),
        },
    )

    return DownloadRunResult(
        run_id=run_id,
        job_name=job.name,
        manifest_path=manifest_path,
        selected_count=len(documents),
        downloaded_count=downloaded_count,
        already_present_count=already_present_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        dry_run=dry_run,
        items=tuple(items),
    )


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    sha256: str
    bytes_written: int
    content_type: str | None


def download_revision_file(
    auth_ctx: AuthContext,
    *,
    revision_id: str,
    target_path: Path,
    fallback_filename: str,
    log_callback: DownloadLogCallback = None,
) -> DownloadedFile:
    url = (
        f"{auth_ctx.base_url}/api/{REVISION_DOWNLOAD_API_VERSION}"
        f"/document_revisions/{revision_id}/download"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.parent / f".{target_path.name}.tmp"

    with _stream_download_response(auth_ctx, url, log_callback=log_callback) as response:
        content_type = response.headers.get("content-type")
        filename = _filename_from_content_disposition(response.headers.get("content-disposition"))
        if filename:
            target_path = target_path.with_name(_safe_filename(filename))
            temp_path = target_path.parent / f".{target_path.name}.tmp"
        elif not target_path.name:
            target_path = target_path / _safe_filename(fallback_filename)
            temp_path = target_path.parent / f".{target_path.name}.tmp"

        sha = hashlib.sha256()
        bytes_written = 0
        with temp_path.open("wb") as fh:
            bytes_written = _write_response_body(response, fh, sha)
        temp_path.replace(target_path)
    return DownloadedFile(
        path=target_path,
        sha256=sha.hexdigest(),
        bytes_written=bytes_written,
        content_type=content_type,
    )


def ensure_download_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS download_runs (
            run_id TEXT PRIMARY KEY,
            job_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            selected_count INTEGER NOT NULL,
            downloaded_count INTEGER NOT NULL,
            already_present_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            dry_run INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS download_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            job_name TEXT NOT NULL,
            document_id TEXT NOT NULL,
            document_name TEXT,
            revision_id TEXT NOT NULL,
            current_revision_id TEXT,
            document_modified_at TEXT,
            latest_at_run INTEGER NOT NULL,
            previous_downloaded_revision_id TEXT,
            previous_was_outdated INTEGER NOT NULL,
            status TEXT NOT NULL,
            file_path TEXT,
            sha256 TEXT,
            bytes INTEGER,
            error TEXT,
            source_refs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES download_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_download_items_document
        ON download_items(document_id, revision_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_download_items_status
        ON download_items(status, created_at);
        """
    )


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Download config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError("Download config root must be an object.")
    return payload


def _runtime_output_dir(value: Any) -> Path:
    if value is None:
        return runtime_path(DEFAULT_DOWNLOAD_DIR)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("download output_dir must be a non-empty string.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else runtime_path(path)


def _parse_job(raw: Any, index: int) -> DownloadJob:
    if not isinstance(raw, dict):
        raise ConfigError(f"download jobs[{index}] must be an object.")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"download jobs[{index}].name must be a non-empty string.")
    sources_raw = _list(raw.get("sources"), f"job[{name}].sources")
    sources = tuple(_parse_source(item, i, name) for i, item in enumerate(sources_raw))
    document_filters = tuple(
        _parse_filter(item, f"job[{name}].document_filters[{i}]")
        for i, item in enumerate(
            _list(raw.get("document_filters", []), f"job[{name}].document_filters")
        )
    )
    max_documents = _optional_positive_int(raw.get("max_documents"), f"job[{name}].max_documents")
    revision_field = _optional_path(raw.get("revision_field"), "latest_revision")
    filename_field = _optional_path(raw.get("filename_field"), "node_name")
    return DownloadJob(
        name=name.strip(),
        sources=sources,
        document_filters=document_filters,
        max_documents=max_documents,
        revision_field=revision_field,
        filename_field=filename_field,
    )


def _parse_source(raw: Any, index: int, job_name: str) -> DownloadSource:
    if not isinstance(raw, dict):
        raise ConfigError(f"job[{job_name}].sources[{index}] must be an object.")
    endpoint = raw.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ConfigError(f"job[{job_name}].sources[{index}].endpoint must be a string.")
    filters = tuple(
        _parse_filter(item, f"job[{job_name}].sources[{index}].filters[{i}]")
        for i, item in enumerate(
            _list(raw.get("filters", []), f"job[{job_name}].sources[{index}].filters")
        )
    )
    paths_raw = raw.get("document_paths", [])
    paths_field = f"job[{job_name}].sources[{index}].document_paths"
    if endpoint.strip() != DOCUMENT_ENDPOINT:
        document_paths = tuple(_string_list(paths_raw, paths_field))
        if not document_paths:
            raise ConfigError(
                f"job[{job_name}].sources[{index}].document_paths is required "
                f"when endpoint is not {DOCUMENT_ENDPOINT!r}."
            )
    else:
        document_paths = tuple(_string_list(paths_raw, paths_field))
    return DownloadSource(endpoint=endpoint.strip(), filters=filters, document_paths=document_paths)


def _parse_filter(raw: Any, field_name: str) -> DownloadFilter:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError(f"{field_name}.path must be a non-empty string.")
    operators = [
        key
        for key in ("equals", "in", "contains", "matches", "exists")
        if key in raw
    ]
    if len(operators) != 1:
        raise ConfigError(f"{field_name} must set exactly one filter operator.")
    in_values = None
    if "in" in raw:
        in_values = tuple(_list(raw.get("in"), f"{field_name}.in"))
    exists = raw.get("exists") if "exists" in raw else None
    if exists is not None and not isinstance(exists, bool):
        raise ConfigError(f"{field_name}.exists must be true or false.")
    matches = raw.get("matches")
    if matches is not None:
        if not isinstance(matches, str):
            raise ConfigError(f"{field_name}.matches must be a string.")
        re.compile(matches)
    return DownloadFilter(
        path=path.strip(),
        equals=raw.get("equals"),
        in_values=in_values,
        contains=raw.get("contains"),
        matches=matches,
        exists=exists,
    )


def _list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be an array.")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    values = _list(value, field_name)
    output: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{field_name}[{index}] must be a non-empty string.")
        output.append(item.strip())
    return output


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer.")
    return value


def _optional_path(value: Any, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("download path fields must be non-empty strings.")
    return value.strip()


def _ensure_unique_job_names(jobs: tuple[DownloadJob, ...]) -> None:
    names: set[str] = set()
    for job in jobs:
        if job.name in names:
            raise ConfigError(f"Duplicate download job name: {job.name}")
        names.add(job.name)


def _select_job(config: DownloadConfig, job_name: str | None) -> DownloadJob:
    if job_name is None:
        return config.jobs[0]
    for job in config.jobs:
        if job.name == job_name:
            return job
    raise ConfigError(f"Unknown download job: {job_name}")


def _collect_candidate_documents(
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
            if not _matches_filters(payload, source.filters):
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
            if source.endpoint == DOCUMENT_ENDPOINT and not source.document_paths:
                candidates.setdefault(str(row["record_id"]), []).append(
                    CandidateDocument(
                        document_id=str(row["record_id"]),
                        source_endpoint=source.endpoint,
                        source_record_id=str(row["record_id"]),
                        source_path="$self",
                    )
                )
            else:
                for path in source.document_paths:
                    for document_id in sorted(
                        set(_document_ids_from_value(_extract_path(payload, path)))
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


def _resolve_documents(
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
        if not _matches_filters(payload, job.document_filters):
            _emit(
                log_callback,
                {
                    "level": "debug",
                    "event": "download_document_filtered",
                    "document_id": document_id,
                },
            )
            continue
        revision_id = _string_value(_extract_path(payload, job.revision_field))
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
        filename = _string_value(_extract_path(payload, job.filename_field)) or f"{document_id}.bin"
        documents.append(
            ResolvedDocument(
                document_id=document_id,
                document_payload=payload,
                latest_revision_id=revision_id,
                filename=filename,
                candidates=tuple(refs),
            )
        )
    return documents


def _matches_filters(payload: dict[str, Any], filters: tuple[DownloadFilter, ...]) -> bool:
    return all(_matches_filter(payload, item) for item in filters)


def _matches_filter(payload: dict[str, Any], item: DownloadFilter) -> bool:
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


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, str) and isinstance(expected, str):
        return expected in value
    if isinstance(value, list):
        return expected in value
    return value == expected


def _extract_path(payload: Any, path: str) -> Any:
    return _extract_path_with_presence(payload, path)[1]


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


def _load_previous_downloads(conn: sqlite3.Connection) -> dict[str, str]:
    ensure_download_tables(conn)
    rows = conn.execute(
        """
        SELECT document_id, revision_id
        FROM download_items
        WHERE status IN ('downloaded', 'already_present')
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    previous: dict[str, str] = {}
    for row in rows:
        previous[str(row["document_id"])] = str(row["revision_id"])
    return previous


def _record_download_run(
    conn: sqlite3.Connection,
    *,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO download_runs (
            run_id, job_name, started_at, finished_at, manifest_path,
            selected_count, downloaded_count, already_present_count,
            failed_count, skipped_count, dry_run
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            manifest["run_id"],
            manifest["job"],
            manifest["started_at"],
            manifest["finished_at"],
            str(manifest_path),
            manifest["selected_count"],
            manifest["downloaded_count"],
            manifest["already_present_count"],
            manifest["failed_count"],
            manifest["skipped_count"],
            int(bool(manifest["dry_run"])),
        ],
    )
    rows = []
    created_at = manifest["finished_at"]
    for item in manifest["items"]:
        rows.append(
            [
                manifest["run_id"],
                manifest["job"],
                item["document_id"],
                item.get("document_name"),
                item["latest_revision_id"],
                item.get("current_revision_id"),
                item.get("document_modified_at"),
                int(bool(item.get("latest_at_run"))),
                item.get("previous_downloaded_revision_id"),
                int(bool(item.get("previous_was_outdated"))),
                item["status"],
                item.get("file_path"),
                item.get("sha256"),
                item.get("bytes"),
                item.get("error"),
                json.dumps(item.get("source_refs", []), sort_keys=True),
                created_at,
            ]
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO download_items (
                run_id, job_name, document_id, document_name, revision_id,
                current_revision_id, document_modified_at, latest_at_run,
                previous_downloaded_revision_id, previous_was_outdated, status,
                file_path, sha256, bytes, error, source_refs_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


@contextmanager
def _stream_download_response(
    auth_ctx: AuthContext,
    url: str,
    *,
    log_callback: DownloadLogCallback,
) -> Iterator[httpx.Response]:
    token = auth_ctx.ensure_token()
    headers = {"Authorization": f"Bearer {token}"}
    started = time.perf_counter()
    stream_cm = auth_ctx.client.stream("GET", url, headers=headers)
    response = stream_cm.__enter__()
    try:
        if response.status_code == 401:
            stream_cm.__exit__(None, None, None)
            token = auth_ctx.refresh_token()
            headers["Authorization"] = f"Bearer {token}"
            stream_cm = auth_ctx.client.stream("GET", url, headers=headers)
            response = stream_cm.__enter__()
        duration_seconds = time.perf_counter() - started
        _emit(
            log_callback,
            {
                "level": "http",
                "event": "download_http_response",
                "url": url,
                "status_code": response.status_code,
                "duration_seconds": round(duration_seconds, 3),
                "content_length": response.headers.get("content-length"),
                "content_type": response.headers.get("content-type"),
            },
        )
        if response.status_code >= 400:
            body = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"download failed with HTTP {response.status_code}: {body}")
        yield response
    finally:
        stream_cm.__exit__(None, None, None)


def _write_response_body(response: httpx.Response, fh: BinaryIO, sha: Any) -> int:
    bytes_written = 0
    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
        if not chunk:
            continue
        fh.write(chunk)
        sha.update(chunk)
        bytes_written += len(chunk)
    return bytes_written


def _filename_from_content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    message = Message()
    message["content-disposition"] = value
    filename = message.get_filename()
    return filename.strip() if filename else None


def _document_target_path(
    files_dir: Path,
    *,
    document_id: str,
    revision_id: str,
    filename: str,
) -> Path:
    return (
        files_dir
        / _safe_path_part(document_id)
        / _safe_path_part(revision_id)
        / _safe_filename(filename)
    )


def _safe_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "unknown"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[/:\\]+", "_", value.strip())
    safe = safe.strip(".")
    return safe or "download.bin"


def _write_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "manifest.json"
    temp_path = run_dir / ".manifest.json.tmp"
    temp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)
    return path


def _allocate_run_id(output_dir: Path, created_at: datetime, job_name: str) -> str:
    safe_job = _safe_path_part(job_name)
    base = f"{created_at:%Y-%m-%dT%H%M%SZ}-{safe_job}"
    for index in range(100):
        suffix = "" if index == 0 else f"-{index + 1}"
        run_id = f"{base}{suffix}"
        if not (output_dir / "runs" / run_id).exists():
            return run_id
    raise RuntimeError("Could not allocate download run id.")


def _datetime_to_db(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _emit(callback: DownloadLogCallback, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)
