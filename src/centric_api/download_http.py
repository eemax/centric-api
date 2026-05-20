from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from .auth import AuthContext

REVISION_DOWNLOAD_API_VERSION = "v2"
DOWNLOAD_RETRY_MAX_ATTEMPTS = 3
DOWNLOAD_RETRY_BASE_SECONDS = 15
DOWNLOAD_RETRY_MAX_SECONDS = 30
RETRYABLE_DOWNLOAD_STATUSES = {408, 429, 500, 502, 503, 504}

DownloadLogCallback = Callable[[dict[str, Any]], None] | None


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    sha256: str
    bytes_written: int
    content_type: str | None


class DownloadHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"download failed with HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


def download_revision_file(
    auth_ctx: AuthContext,
    *,
    revision_id: str,
    target_path: Path,
    fallback_filename: str,
    log_callback: DownloadLogCallback = None,
) -> DownloadedFile:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRY_MAX_ATTEMPTS + 1):
        _emit(
            log_callback,
            {
                "level": "http",
                "event": "download_attempt",
                "revision_id": revision_id,
                "attempt": attempt,
                "max_attempts": DOWNLOAD_RETRY_MAX_ATTEMPTS,
            },
        )
        try:
            return _download_revision_file_once(
                auth_ctx,
                revision_id=revision_id,
                target_path=target_path,
                fallback_filename=fallback_filename,
                log_callback=log_callback,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= DOWNLOAD_RETRY_MAX_ATTEMPTS or not _is_retryable_download_error(exc):
                raise
            delay_seconds = _download_retry_delay(attempt)
            _emit(
                log_callback,
                {
                    "level": "summary",
                    "event": "download_retry",
                    "revision_id": revision_id,
                    "attempt": attempt,
                    "delay_seconds": delay_seconds,
                    "error": str(exc),
                    "status_code": (
                        exc.status_code if isinstance(exc, DownloadHTTPError) else None
                    ),
                },
            )
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("download failed unexpectedly.")


def _download_revision_file_once(
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
        try:
            with temp_path.open("wb") as fh:
                bytes_written = _write_response_body(response, fh, sha)
            temp_path.replace(target_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    return DownloadedFile(
        path=target_path,
        sha256=sha.hexdigest(),
        bytes_written=bytes_written,
        content_type=content_type,
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
            raise DownloadHTTPError(response.status_code, body)
        yield response
    finally:
        stream_cm.__exit__(None, None, None)


def _is_retryable_download_error(exc: Exception) -> bool:
    if isinstance(exc, DownloadHTTPError):
        return exc.status_code in RETRYABLE_DOWNLOAD_STATUSES
    return isinstance(exc, httpx.TransportError)


def _download_retry_delay(failed_attempt: int) -> int:
    return min(
        DOWNLOAD_RETRY_BASE_SECONDS * (2 ** (failed_attempt - 1)),
        DOWNLOAD_RETRY_MAX_SECONDS,
    )


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


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[/:\\]+", "_", value.strip())
    safe = safe.strip(".")
    return safe or "download.bin"


def _emit(callback: DownloadLogCallback, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)
