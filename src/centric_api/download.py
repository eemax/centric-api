from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifact_names import allocate_artifact_name, artifact_base_name
from .auth import AuthContext
from .config import ConfigError
from .db_schema import ensure_download_tables
from .download_config import (
    DownloadConfig,
    select_download_job,
)
from .download_http import download_revision_file
from .download_selection import (
    ResolvedDocument,
    collect_candidate_documents,
    extract_path,
    preflight_download_cache,
    resolve_documents,
    string_value,
)
from .download_state import (
    count_unselected_current,
    record_download_run,
    tombstone_unselected_current,
    update_download_current,
    write_manifest,
)
from .store import connect, connect_readonly, table_exists

DownloadLogCallback = Callable[[dict[str, Any]], None] | None
DownloadProgressCallback = Callable[[dict[str, Any]], None] | None
DOWNLOAD_MODES = {"delta", "sync", "rebuild"}


@dataclass(frozen=True)
class DownloadRunResult:
    run_id: str
    job_name: str
    mode: str
    manifest_path: Path
    matched_count: int
    selected_count: int
    downloaded_count: int
    already_present_count: int
    failed_count: int
    skipped_count: int
    skipped_current_count: int
    dry_run_count: int
    superseded_count: int
    tombstoned_count: int
    dry_run: bool
    items: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CurrentDownload:
    document_id: str
    revision_id: str
    status: str
    file_path: str | None
    sha256: str | None
    bytes: int | None


@dataclass(frozen=True)
class ExistingDownloadFile:
    path: Path
    sha256: str
    bytes: int


def run_download_job(
    *,
    db_path: Path,
    auth_ctx: AuthContext | None,
    config: DownloadConfig,
    job_name: str | None = None,
    mode: str = "delta",
    dry_run: bool = False,
    log_callback: DownloadLogCallback = None,
    progress_callback: DownloadProgressCallback = None,
) -> DownloadRunResult:
    if mode not in DOWNLOAD_MODES:
        raise ConfigError(f"download mode must be one of: {', '.join(sorted(DOWNLOAD_MODES))}.")
    job = select_download_job(config, job_name)
    started = time.time()
    created_at = datetime.now(UTC)
    files_dir = config.output_dir / "files"

    with _connect_for_download(db_path, dry_run=dry_run) as conn:
        if not dry_run:
            ensure_download_tables(conn)
        run_id = _allocate_run_id(config.output_dir, conn, created_at, job.name)
        run_dir = config.output_dir / "runs" / run_id
        _emit(
            log_callback,
            {
                "level": "summary",
                "event": "download_start",
                "run_id": run_id,
                "job": job.name,
                "mode": mode,
                "config": str(config.path),
                "db": str(db_path),
                "dry_run": dry_run,
            },
        )
        preflight_download_cache(conn, job)
        candidates = collect_candidate_documents(conn, job, log_callback=log_callback)
        documents = resolve_documents(conn, job, candidates, log_callback=log_callback)
        current_downloads = _load_current_downloads(conn, job.name)
        desired_documents = {document.document_id: document for document in documents}
        tombstoned_count = (
            count_unselected_current(
                conn,
                job_name=job.name,
                desired_documents=desired_documents,
            )
            if mode == "rebuild" and not dry_run
            else 0
        )

    matched_count = len(documents)
    selected_documents, skipped_current_items = _select_documents_for_mode(
        documents=documents,
        mode=mode,
        files_dir=files_dir,
        current_downloads=current_downloads,
    )

    _emit_progress(
        progress_callback,
        {
            "event": "download_start",
            "run_id": run_id,
            "job": job.name,
            "mode": mode,
            "matched": matched_count,
            "selected": len(selected_documents),
            "skipped_current": len(skipped_current_items),
        },
    )

    items: list[dict[str, Any]] = []
    downloaded_count = 0
    already_present_count = 0
    failed_count = 0
    skipped_count = len(skipped_current_items)
    skipped_current_count = len(skipped_current_items)
    dry_run_count = 0
    superseded_count = 0
    for skipped_item in skipped_current_items:
        items.append(skipped_item)

    for index, document in enumerate(selected_documents, start=1):
        previous_current = current_downloads.get(document.document_id)
        previous_revision = previous_current.revision_id if previous_current else None
        target_path = _document_target_path(
            files_dir,
            document_id=document.document_id,
            revision_id=document.latest_revision_id,
            filename=document.filename,
        )
        existing_file = _existing_latest_file(
            document=document,
            target_path=target_path,
            current=previous_current,
        )
        base_item = _base_item(
            document,
            previous_revision=previous_revision,
            target_path=target_path,
        )
        if dry_run:
            item = {**base_item, "status": "dry_run"}
            skipped_count += 1
            dry_run_count += 1
        elif mode != "rebuild" and existing_file is not None:
            item = {
                **base_item,
                "status": "already_present",
                "file_path": str(existing_file.path),
                "sha256": existing_file.sha256,
                "bytes": existing_file.bytes,
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
        _emit_download_item(log_callback, item)
        _emit_progress(
            progress_callback,
            {
                "event": "download_item",
                "index": index,
                "total": len(selected_documents),
                "document_id": item["document_id"],
                "revision_id": item["latest_revision_id"],
                "status": item["status"],
                "bytes": item.get("bytes"),
                "error": item.get("error"),
                "elapsed_seconds": round(time.time() - started, 3),
            },
        )

    superseded_count = _count_superseded_current(
        items=items,
        current_downloads=current_downloads,
    )

    duration_seconds = time.time() - started
    manifest = {
        "run_id": run_id,
        "job": job.name,
        "mode": mode,
        "config": str(config.path),
        "db": str(db_path),
        "dry_run": dry_run,
        "started_at": _datetime_to_db(created_at),
        "finished_at": _datetime_to_db(datetime.now(UTC)),
        "duration_seconds": round(duration_seconds, 3),
        "matched_count": matched_count,
        "selected_count": len(selected_documents),
        "downloaded_count": downloaded_count,
        "already_present_count": already_present_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "skipped_current_count": skipped_current_count,
        "dry_run_count": dry_run_count,
        "superseded_count": superseded_count,
        "tombstoned_count": tombstoned_count,
        "items": items,
    }
    manifest_path = run_dir / "manifest.json"
    if dry_run:
        return DownloadRunResult(
            run_id=run_id,
            job_name=job.name,
            mode=mode,
            manifest_path=manifest_path,
            matched_count=matched_count,
            selected_count=len(selected_documents),
            downloaded_count=downloaded_count,
            already_present_count=already_present_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            skipped_current_count=skipped_current_count,
            dry_run_count=dry_run_count,
            superseded_count=superseded_count,
            tombstoned_count=tombstoned_count,
            dry_run=dry_run,
            items=tuple(items),
        )

    manifest_path = write_manifest(run_dir, manifest)

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        if mode == "rebuild":
            tombstone_unselected_current(
                conn,
                job_name=job.name,
                desired_documents=desired_documents,
                run_id=run_id,
                tombstoned_at=manifest["finished_at"],
            )
        record_download_run(conn, manifest_path=manifest_path, manifest=manifest)
        update_download_current(conn, manifest=manifest)

    status = "failed" if failed_count and failed_count == len(selected_documents) else "ok"
    if failed_count and failed_count < len(selected_documents):
        status = "partial"
    _emit(
        log_callback,
        {
            "level": "summary",
            "event": f"download_{status}",
            "run_id": run_id,
            "job": job.name,
            "mode": mode,
            "matched": matched_count,
            "selected": len(selected_documents),
            "downloaded": downloaded_count,
            "already_present": already_present_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "skipped_current": skipped_current_count,
            "dry_run": dry_run_count,
            "superseded": superseded_count,
            "tombstoned": tombstoned_count,
            "duration_seconds": round(duration_seconds, 3),
            "manifest": str(manifest_path),
        },
    )

    return DownloadRunResult(
        run_id=run_id,
        job_name=job.name,
        mode=mode,
        manifest_path=manifest_path,
        matched_count=matched_count,
        selected_count=len(selected_documents),
        downloaded_count=downloaded_count,
        already_present_count=already_present_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        skipped_current_count=skipped_current_count,
        dry_run_count=dry_run_count,
        superseded_count=superseded_count,
        tombstoned_count=tombstoned_count,
        dry_run=dry_run,
        items=tuple(items),
    )


def _base_item(
    document: ResolvedDocument,
    *,
    previous_revision: str | None,
    target_path: Path,
) -> dict[str, Any]:
    return {
        "document_id": document.document_id,
        "document_name": document.filename,
        "latest_revision_id": document.latest_revision_id,
        "current_revision_id": string_value(
            extract_path(document.document_payload, "current_revision")
        ),
        "document_modified_at": string_value(
            extract_path(document.document_payload, "_modified_at")
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


def _emit_download_item(log_callback: DownloadLogCallback, item: dict[str, Any]) -> None:
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


def _connect_for_download(db_path: Path, *, dry_run: bool) -> sqlite3.Connection:
    return connect_readonly(db_path) if dry_run else connect(db_path)


def _load_current_downloads(conn: sqlite3.Connection, job_name: str) -> dict[str, CurrentDownload]:
    if not table_exists(conn, "download_current"):
        return {}
    rows = conn.execute(
        """
        SELECT document_id, revision_id, status, file_path, sha256, bytes
        FROM download_current
        WHERE job_name = ? AND status = 'current'
        ORDER BY selected_at ASC
        """,
        [job_name],
    ).fetchall()
    current: dict[str, CurrentDownload] = {}
    for row in rows:
        current[str(row["document_id"])] = CurrentDownload(
            document_id=str(row["document_id"]),
            revision_id=str(row["revision_id"]),
            status=str(row["status"]),
            file_path=row["file_path"],
            sha256=row["sha256"],
            bytes=row["bytes"],
        )
    return current


def _select_documents_for_mode(
    *,
    documents: list[ResolvedDocument],
    mode: str,
    files_dir: Path,
    current_downloads: dict[str, CurrentDownload],
) -> tuple[list[ResolvedDocument], list[dict[str, Any]]]:
    if mode in {"sync", "rebuild"}:
        return documents, []

    selected: list[ResolvedDocument] = []
    skipped: list[dict[str, Any]] = []
    for document in documents:
        current = current_downloads.get(document.document_id)
        target_path = _document_target_path(
            files_dir,
            document_id=document.document_id,
            revision_id=document.latest_revision_id,
            filename=document.filename,
        )
        existing_file = _existing_latest_file(
            document=document,
            target_path=target_path,
            current=current,
        )
        if current is not None and existing_file is not None:
            skipped.append(
                {
                    **_base_item(
                        document,
                        previous_revision=current.revision_id,
                        target_path=existing_file.path,
                    ),
                    "status": "skipped_current",
                    "sha256": existing_file.sha256,
                    "bytes": existing_file.bytes,
                }
            )
        else:
            selected.append(document)
    return selected, skipped


def _existing_latest_file(
    *,
    document: ResolvedDocument,
    target_path: Path,
    current: CurrentDownload | None,
) -> ExistingDownloadFile | None:
    if current is not None and current.revision_id == document.latest_revision_id:
        current_path = Path(current.file_path) if current.file_path else target_path
        existing = _verified_existing_file(current_path, current=current)
        if existing is not None:
            return existing
        if current_path.is_file():
            return None
    if target_path.is_file():
        return _verified_existing_file(target_path)
    return None


def _verified_existing_file(
    path: Path,
    *,
    current: CurrentDownload | None = None,
) -> ExistingDownloadFile | None:
    if not path.is_file():
        return None
    size = path.stat().st_size
    if current is not None and current.bytes is not None and current.bytes != size:
        return None
    sha256 = _sha256(path)
    if current is not None and current.sha256 and current.sha256 != sha256:
        return None
    return ExistingDownloadFile(path=path, sha256=sha256, bytes=size)


def _count_superseded_current(
    *,
    items: list[dict[str, Any]],
    current_downloads: dict[str, CurrentDownload],
) -> int:
    count = 0
    for item in items:
        if item["status"] not in {"downloaded", "already_present"}:
            continue
        current = current_downloads.get(str(item["document_id"]))
        if current is not None and current.revision_id != item["latest_revision_id"]:
            count += 1
    return count


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


def _allocate_run_id(
    output_dir: Path,
    conn: sqlite3.Connection,
    created_at: datetime,
    job_name: str,
) -> str:
    base = artifact_base_name(job_name, created_at)
    return allocate_artifact_name(
        base,
        lambda run_id: (output_dir / "runs" / run_id).exists()
        or _download_run_exists(conn, run_id),
        limit=100,
    )


def _download_run_exists(conn: sqlite3.Connection, run_id: str) -> bool:
    if not table_exists(conn, "download_runs"):
        return False
    row = conn.execute("SELECT 1 FROM download_runs WHERE run_id = ?", [run_id]).fetchone()
    return row is not None


def _datetime_to_db(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _emit(callback: DownloadLogCallback, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)


def _emit_progress(callback: DownloadProgressCallback, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)
