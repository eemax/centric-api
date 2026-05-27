from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal, TextIO

LOG_LEVEL_RANKS = {"off": 0, "summary": 1, "http": 2, "debug": 3}

LogLevel = Literal["off", "summary", "http", "debug"]
LogEvent = dict[str, Any]
LogCallback = Callable[[LogEvent], None]
UtcIsoFn = Callable[[], str]


def build_log_callback(
    log_file: TextIO,
    *,
    log_level: LogLevel,
    utc_iso: UtcIsoFn,
) -> LogCallback:
    selected_rank = LOG_LEVEL_RANKS[log_level]

    def _log(event: LogEvent) -> None:
        event_level = str(event.get("level", "summary")).lower()
        event_rank = LOG_LEVEL_RANKS.get(event_level, LOG_LEVEL_RANKS["debug"])
        if event_rank > selected_rank:
            return
        line = render_log_line({"timestamp": utc_iso(), **event})
        log_file.write(line + "\n")
        log_file.flush()

    return _log


def render_log_line(record: LogEvent) -> str:
    event = str(record.get("event", "event"))
    pieces = [str(record.get("timestamp", "")), _log_label(record)]
    for key in _log_key_order(event, record):
        value = record.get(key)
        if value is None:
            continue
        pieces.append(f"{_log_key(key)}={_log_value(key, value)}")
    return " ".join(pieces)


def format_duration(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1:
        return f"{value * 1000:.0f}ms"
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(int(round(value)), 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_seconds(value: float | None) -> str:
    seconds = value if value is not None else 0.0
    return f"{seconds:.2f}s"


def _log_label(record: LogEvent) -> str:
    event = str(record.get("event", "event"))
    level = str(record.get("level", "summary")).lower()
    labels = {
        "run_start": "RUN start",
        "run_ok": "RUN ok",
        "run_partial": "RUN partial",
        "run_failed": "RUN failed",
        "manifest_failed": "MANIFEST failed",
        "endpoint_start": "ENDPOINT start",
        "endpoint_ok": "ENDPOINT ok",
        "endpoint_failed": "ENDPOINT failed",
        "ingest_ok": "INGEST ok",
        "ingest_skipped": "INGEST skipped",
        "ingest_failed": "INGEST failed",
        "changelog_ok": "CHANGELOG ok",
        "changelog_skipped": "CHANGELOG skipped",
        "changelog_failed": "CHANGELOG failed",
        "request_failed": "REQUEST failed",
        "http_request": "HTTP request",
        "http_response": "HTTP response",
        "count_preflight": "HTTP count",
        "data_page": "HTTP page",
        "retry_scheduled": "RETRY scheduled",
        "download_start": "DOWNLOAD start",
        "download_item": "DOWNLOAD item",
        "download_attempt": "DOWNLOAD attempt",
        "download_retry": "DOWNLOAD retry",
        "download_ok": "DOWNLOAD ok",
        "download_partial": "DOWNLOAD partial",
        "download_failed": "DOWNLOAD failed",
        "download_document_missing": "DOWNLOAD missing_document",
        "download_revision_missing": "DOWNLOAD missing_revision",
        "download_revision_record_missing": "DOWNLOAD missing_revision_record",
        "download_revision_filtered": "DOWNLOAD filtered_revision",
        "download_http_response": "DOWNLOAD http",
    }
    if event in labels:
        return labels[event]
    if level == "debug":
        return f"DEBUG {event}"
    return event


def _log_key_order(event: str, record: LogEvent) -> list[str]:
    preferred = {
        "run_start": [
            "run_id",
            "mode",
            "endpoint_count",
            "endpoints",
            "modified_since",
            "overlap_minutes",
            "delta_state_file",
            "output_dir",
        ],
        "run_ok": _run_log_keys(),
        "run_partial": _run_log_keys(),
        "run_failed": _run_log_keys(),
        "manifest_failed": ["error"],
        "endpoint_start": ["endpoint", "mode", "delta_floor"],
        "endpoint_ok": [
            "endpoint",
            "expected",
            "fetched",
            "pages",
            "retries",
            "duration_seconds",
            "output",
            "count_validation",
            "id_validation",
            "unique_ids",
        ],
        "endpoint_failed": ["endpoint", "mode", "duration_seconds", "error"],
        "ingest_ok": [
            "applied_files",
            "skipped_files",
            "records_read",
            "upserts",
            "deletes",
            "hard_deletes",
            "invalid",
        ],
        "changelog_ok": ["events", "scoped", "run_id"],
        "changelog_skipped": ["reason"],
        "changelog_failed": ["error"],
        "request_failed": [
            "endpoint",
            "request_kind",
            "method",
            "url",
            "reason",
            "status_code",
            "attempt",
            "max_attempts",
            "error",
        ],
        "http_request": [
            "endpoint",
            "request_kind",
            "method",
            "url",
            "attempt",
            "max_attempts",
        ],
        "http_response": [
            "endpoint",
            "request_kind",
            "status_code",
            "duration_seconds",
            "attempt",
            "max_attempts",
            "reason_phrase",
            "url",
        ],
        "count_preflight": ["endpoint", "expected"],
        "data_page": ["endpoint", "skip", "limit", "items", "duration_seconds"],
        "retry_scheduled": [
            "endpoint",
            "request_kind",
            "reason",
            "attempt",
            "next_attempt",
            "max_attempts",
            "sleep_seconds",
            "status_code",
            "error",
        ],
        "ingest_skipped": ["reason"],
        "ingest_failed": ["error"],
        "download_start": ["run_id", "job", "mode", "config", "db", "dry_run"],
        "download_item": ["document_id", "revision_id", "status", "file"],
        "download_attempt": ["revision_id", "attempt", "max_attempts"],
        "download_retry": ["revision_id", "attempt", "delay_seconds", "error", "status_code"],
        "download_ok": _download_log_keys(),
        "download_partial": _download_log_keys(),
        "download_failed": _download_log_keys(),
        "download_document_missing": ["document_id"],
        "download_revision_missing": ["document_id"],
        "download_revision_record_missing": ["document_id", "revision_id"],
        "download_revision_filtered": ["document_id", "revision_id"],
        "download_http_response": [
            "status_code",
            "duration_seconds",
            "content_length",
            "content_type",
            "url",
        ],
    }
    keys = preferred.get(event, [])
    remaining = sorted(
        key for key in record if key not in {"timestamp", "level", "event"} and key not in keys
    )
    return [*keys, *remaining]


def _run_log_keys() -> list[str]:
    return [
        "run_id",
        "mode",
        "endpoints_ok",
        "endpoints_failed",
        "endpoints_total",
        "fetched",
        "pages",
        "retries",
        "duration_seconds",
        "manifest",
        "pipeline_error",
    ]


def _download_log_keys() -> list[str]:
    return [
        "run_id",
        "job",
        "mode",
        "matched",
        "selected",
        "downloaded",
        "already_present",
        "failed",
        "skipped",
        "skipped_current",
        "dry_run",
        "superseded",
        "tombstoned",
        "duration_seconds",
        "manifest",
    ]


def _log_key(key: str) -> str:
    return {
        "duration_seconds": "duration",
        "sleep_seconds": "sleep",
    }.get(key, key)


def _log_value(key: str, value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        if key.endswith("_seconds"):
            return f"{text}s"
        return text
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if not text or any(char.isspace() for char in text):
        return json.dumps(text, ensure_ascii=True)
    return text
