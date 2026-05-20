from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TextIO

from .bundle import BundleComparison, BundleRunResult
from .changelog import ChangelogRun
from .download import DownloadRunResult
from .models import EndpointSpec, FetchProgressEvent, FetchRunResult
from .store import IngestResult

LOG_LEVEL_RANKS = {"off": 0, "summary": 1, "http": 2, "debug": 3}

LogLevel = Literal["off", "summary", "http", "debug"]
LogEvent = dict[str, Any]
LogCallback = Callable[[LogEvent], None]
UtcIsoFn = Callable[[], str]


def _write_progress_line(event: FetchProgressEvent) -> None:
    if event.kind == "endpoint_start":
        expected = event.expected_count if event.expected_count is not None else "unknown"
        print(
            f"[{event.endpoint}] start: skip={event.start_skip} limit={event.limit} "
            f"expected={expected} retries={event.retries_used} "
            f"elapsed={_format_seconds(event.elapsed_seconds)}",
            file=sys.stderr,
        )
        return
    if event.kind == "page_fetched":
        page_label = str(event.page_index)
        if event.expected_pages is not None:
            page_label = f"{page_label}/{event.expected_pages}"
        line = (
            f"[{event.endpoint}] page {page_label}: page_items={event.page_items} "
            f"total_items={event.items_fetched} skip={event.skip} next_skip={event.next_skip} "
            f"elapsed={_format_seconds(event.elapsed_seconds)}"
        )
        if event.percent_complete is not None:
            line += f" progress={event.percent_complete:.1f}%"
        if event.rolling_avg_seconds is not None:
            line += f" avg_page={_format_duration(event.rolling_avg_seconds)}"
        if event.estimated_remaining_seconds is not None:
            line += f" eta={_format_duration(event.estimated_remaining_seconds)}"
        print(line, file=sys.stderr)
        return
    if event.kind == "warning":
        print(f"[{event.endpoint}] warning: {event.message}", file=sys.stderr)
        return
    if event.kind == "endpoint_finish":
        print(
            f"[{event.endpoint}] finish: pages={event.pages_fetched} items={event.items_fetched} "
            f"retries={event.retries_used} warnings={event.warnings_count} "
            f"elapsed={_format_seconds(event.elapsed_seconds)}",
            file=sys.stderr,
        )


def _write_download_progress_line(event: dict[str, Any]) -> None:
    if event.get("event") == "download_start":
        print(
            f"[download] start: job={event.get('job')} mode={event.get('mode')} "
            f"matched={event.get('matched')} selected={event.get('selected')} "
            f"skipped_current={event.get('skipped_current')}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "download_item":
        line = (
            f"[download] {event.get('index')}/{event.get('total')} "
            f"{event.get('status')} document={event.get('document_id')} "
            f"revision={event.get('revision_id')} "
            f"elapsed={_format_seconds(event.get('elapsed_seconds'))}"
        )
        if event.get("bytes") is not None:
            line += f" bytes={event.get('bytes')}"
        if event.get("error"):
            line += f" error={json.dumps(event.get('error'))}"
        print(line, file=sys.stderr)


def _write_json_download_progress(event: dict[str, Any]) -> None:
    print(json.dumps({"record_type": event.get("event"), **event}, default=str))


def _print_human_fetch_summary(
    *,
    mode: str,
    run_id: str,
    raw_dir: Path,
    selected_count: int,
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    duration_seconds: float,
    ingest_result: IngestResult | None,
    changelog_run: ChangelogRun | None,
    changelog_skipped: str | None,
    pipeline_error: str | None,
) -> None:
    title = (
        "Fetch Complete" if not failures and not pipeline_error else "Fetch Finished With Failures"
    )
    print(title)
    print()
    print(f"Mode: {mode}")
    print(f"Run:  {run_id}")
    print(f"Raw:  {raw_dir}")
    print()
    print("Summary")
    print(f"Endpoints: {len(results)} ok, {len(failures)} failed, {selected_count} total")
    print(f"Records:   {sum(result.items_fetched for result in results)} fetched")
    print(f"Pages:     {sum(result.pages_fetched for result in results)} fetched")
    print(f"Time:      {_format_duration(duration_seconds)}")
    print(f"Retries:   {sum(result.retries_used for result in results)}")
    if results:
        endpoint_width = max(len("Endpoint"), *(len(result.endpoint) for result in results))
        header = f"{'Endpoint':<{endpoint_width}}  {'Records':>7}  {'Expected':>8}  {'Pages':>5}"
        print()
        print(header)
        print("-" * len(header))
        for result in results:
            print(
                f"{result.endpoint:<{endpoint_width}}  "
                f"{result.items_fetched:>7}  {result.expected_count:>8}  "
                f"{result.pages_fetched:>5}"
            )
    if ingest_result is not None:
        print()
        print("Ingest")
        print(
            f"Files:     {ingest_result.applied_files} applied, "
            f"{ingest_result.skipped_files} skipped"
        )
        print(f"Records:   {ingest_result.records_read} read")
        print(f"Upserts:   {ingest_result.records_upserted}")
        print(f"Deletes:   {ingest_result.records_deleted}")
        print(f"Hard del:  {ingest_result.records_hard_deleted}")
        if ingest_result.invalid_records:
            print(f"Invalid:   {ingest_result.invalid_records}")
    if changelog_run is not None:
        print()
        print("Changelog")
        print(f"Events:    {changelog_run.event_count}")
        print(f"Scoped:    {changelog_run.scoped_record_count}")
        print(f"Run:       {changelog_run.run_id}")
    elif changelog_skipped:
        print()
        print(f"Changelog: {changelog_skipped}.")
    if pipeline_error:
        print()
        print("Pipeline")
        print(f"- {pipeline_error}")
    if failures:
        print()
        print("Failures")
        for endpoint, message in failures:
            print(f"- {endpoint}: {message}")


def _print_json_fetch_records(
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    *,
    manifest_path: Path,
    ingest_result: IngestResult | None,
    changelog_run: ChangelogRun | None,
    changelog_skipped: str | None,
    pipeline_error: str | None,
) -> None:
    for result in results:
        print(
            json.dumps(
                {
                    "endpoint": result.endpoint,
                    "status": "ok",
                    "items_fetched": result.items_fetched,
                    "pages_fetched": result.pages_fetched,
                    "expected_count": result.expected_count,
                    "retries_used": result.retries_used,
                    "output_file": str(result.output_file) if result.output_file_created else None,
                    "output_file_created": result.output_file_created,
                }
            )
        )
    for endpoint, message in failures:
        print(json.dumps({"endpoint": endpoint, "status": "failed", "error": message}))
    print(
        json.dumps(
            {
                "record_type": "pipeline_summary",
                "manifest": str(manifest_path),
                "ingest": _ingest_record(ingest_result),
                "changelog": _changelog_record(changelog_run, changelog_skipped),
                "pipeline_error": pipeline_error,
            }
        )
    )


def _print_human_download_summary(result: DownloadRunResult) -> None:
    title = "Download Complete" if not result.failed_count else "Download Finished With Failures"
    print(title)
    print()
    print(f"Job:      {result.job_name}")
    print(f"Mode:     {result.mode}")
    print(f"Run:      {result.run_id}")
    print(f"Manifest: {result.manifest_path}")
    print()
    print("Summary")
    print(f"Matched:         {result.matched_count}")
    print(f"Selected:        {result.selected_count}")
    print(f"Downloaded:      {result.downloaded_count}")
    print(f"Already present: {result.already_present_count}")
    print(f"Skipped total:   {result.skipped_count}")
    print(f"Skipped current: {result.skipped_current_count}")
    print(f"Dry run:         {result.dry_run_count}")
    print(f"Superseded:      {result.superseded_count}")
    print(f"Tombstoned:      {result.tombstoned_count}")
    print(f"Failed:          {result.failed_count}")
    if result.items:
        rows = result.items[:10]
        width = max(len("Document"), *(len(str(row["document_id"])) for row in rows))
        print()
        print(f"{'Document':<{width}}  {'Revision':<12}  Status")
        print("-" * (width + 23))
        for row in rows:
            print(
                f"{str(row['document_id']):<{width}}  "
                f"{str(row['latest_revision_id']):<12}  {row['status']}"
            )
        if len(result.items) > len(rows):
            print(f"... {len(result.items) - len(rows)} more")


def _print_human_bundle_summary(result: BundleRunResult) -> None:
    print("Bundle Complete")
    print()
    print(f"Job:       {result.bundle_name}")
    print(f"Download:  {result.download_job}")
    print(f"Run:       {result.run_id}")
    print(f"Manifest:  {result.manifest_path}")
    print(f"Changelog: {result.changelog_md_path}")
    if result.zip_path is not None:
        print(f"Zip:       {result.zip_path}")
    print()
    print("Summary")
    print(f"Items:     {result.item_count}")
    print(f"Added:     {result.added_count}")
    print(f"Changed:   {result.changed_count}")
    print(f"Renamed:   {result.renamed_count}")
    print(f"Removed:   {result.removed_count}")
    print(f"Unchanged: {result.unchanged_count}")
    print(f"Missing:   {result.missing_count}")


def _print_human_bundle_show(run: dict[str, Any], items: list[dict[str, Any]]) -> None:
    print("Bundle Run")
    print()
    print(f"Run:       {run['run_id']}")
    print(f"Bundle:    {run['bundle_name']}")
    print(f"Download:  {run['download_job']}")
    print(f"Finished:  {run['finished_at']}")
    print(f"Zip:       {run.get('zip_path') or 'none'}")
    print()
    print("Summary")
    print(f"Items:     {run['item_count']}")
    print(f"Added:     {run['added_count']}")
    print(f"Changed:   {run['changed_count']}")
    print(f"Renamed:   {run['renamed_count']}")
    print(f"Removed:   {run['removed_count']}")
    print(f"Unchanged: {run['unchanged_count']}")
    if items:
        print()
        print("Files")
        for item in items[:20]:
            print(f"- {item['change_type']}: {item['archive_path']}")
        if len(items) > 20:
            print(f"... {len(items) - 20} more")


def _print_human_bundle_changelog(comparison: BundleComparison) -> None:
    summary = comparison.summary
    print("Bundle Changelog")
    print()
    print(f"Bundle: {comparison.from_run['bundle_name']}")
    print(f"From:   {comparison.from_run['run_id']}")
    print(f"To:     {comparison.to_run['run_id']}")
    print()
    print("Summary")
    print(f"Added:     {summary['added_count']}")
    print(f"Changed:   {summary['changed_count']}")
    print(f"Renamed:   {summary['renamed_count']}")
    print(f"Removed:   {summary['removed_count']}")
    print(f"Unchanged: {summary['unchanged_count']}")
    changed_items = [item for item in comparison.items if item["change_type"] != "unchanged"]
    if changed_items:
        print()
        print("Changes")
        for item in changed_items[:50]:
            print(f"- {item['change_type']}: {item['archive_path']}")
            if item["change_type"] == "renamed":
                print(f"  Previous path: {item.get('previous_archive_path') or 'unknown'}")
            elif item["change_type"] == "changed":
                print(f"  Previous revision: {item.get('previous_revision_id') or 'unknown'}")
                print(f"  Current revision: {item['revision_id']}")
        if len(changed_items) > 50:
            print(f"... {len(changed_items) - 50} more")


def _build_log_callback(
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
        line = _render_log_line({"timestamp": utc_iso(), **event})
        log_file.write(line + "\n")
        log_file.flush()

    return _log


def _render_log_line(record: LogEvent) -> str:
    event = str(record.get("event", "event"))
    pieces = [str(record.get("timestamp", "")), _log_label(record)]
    for key in _log_key_order(event, record):
        value = record.get(key)
        if value is None:
            continue
        pieces.append(f"{_log_key(key)}={_log_value(key, value)}")
    return " ".join(pieces)


def _log_label(record: LogEvent) -> str:
    event = str(record.get("event", "event"))
    level = str(record.get("level", "summary")).lower()
    labels = {
        "run_start": "RUN start",
        "run_ok": "RUN ok",
        "run_partial": "RUN partial",
        "run_failed": "RUN failed",
        "endpoint_start": "ENDPOINT start",
        "endpoint_ok": "ENDPOINT ok",
        "endpoint_failed": "ENDPOINT failed",
        "ingest_ok": "INGEST ok",
        "ingest_skipped": "INGEST skipped",
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


def _print_delta_dry_run(
    spec: EndpointSpec,
    *,
    delta_floor: str | None,
    overlap_days: int,
    overlap_minutes: int,
) -> None:
    data_modified = spec.query_params.get("_modified_at=ge")
    count_modified = spec.count_spec.query_params.get("_modified_at=ge")
    print(
        json.dumps(
            {
                "endpoint": spec.name,
                "status": "delta_dry_run",
                "overlap_days": overlap_days,
                "overlap_minutes": overlap_minutes,
                "delta_floor": delta_floor,
                "data_modified_at": data_modified,
                "count_modified_at": count_modified,
            }
        )
    )


def _print_rows(rows: list[dict[str, Any]], as_json: bool, *, empty_message: str) -> int:
    if as_json:
        for row in rows:
            print(json.dumps(row, default=str))
        return 0
    if not rows:
        print(empty_message)
        return 0
    for row in rows:
        print(" ".join(f"{key}={json.dumps(value, default=str)}" for key, value in row.items()))
    return 0


def _print_or_json(as_json: bool, payload: dict[str, Any], message: str) -> None:
    print(json.dumps(payload, default=str) if as_json else message)


def _ingest_record(result: IngestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "applied_files": result.applied_files,
        "skipped_files": result.skipped_files,
        "records_read": result.records_read,
        "records_upserted": result.records_upserted,
        "records_deleted": result.records_deleted,
        "records_hard_deleted": result.records_hard_deleted,
        "invalid_records": result.invalid_records,
    }


def _changelog_record(run: ChangelogRun | None, skipped: str | None) -> dict[str, Any]:
    if run is None:
        return {"status": "skipped", "reason": skipped}
    return {
        "status": "updated",
        "run_id": run.run_id,
        "endpoint_count": run.endpoint_count,
        "record_count": run.record_count,
        "event_count": run.event_count,
        "full_refresh": run.full_refresh,
        "scoped_record_count": run.scoped_record_count,
    }


def _download_record(result: DownloadRunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "job": result.job_name,
        "mode": result.mode,
        "manifest": str(result.manifest_path),
        "matched_count": result.matched_count,
        "selected_count": result.selected_count,
        "downloaded_count": result.downloaded_count,
        "already_present_count": result.already_present_count,
        "failed_count": result.failed_count,
        "skipped_count": result.skipped_count,
        "skipped_current_count": result.skipped_current_count,
        "dry_run_count": result.dry_run_count,
        "superseded_count": result.superseded_count,
        "tombstoned_count": result.tombstoned_count,
        "dry_run": result.dry_run,
    }


def _bundle_record(result: BundleRunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "bundle": result.bundle_name,
        "download_job": result.download_job,
        "manifest": str(result.manifest_path),
        "changelog_json": str(result.changelog_json_path),
        "changelog_md": str(result.changelog_md_path),
        "zip": str(result.zip_path) if result.zip_path else None,
        "item_count": result.item_count,
        "added_count": result.added_count,
        "changed_count": result.changed_count,
        "renamed_count": result.renamed_count,
        "removed_count": result.removed_count,
        "unchanged_count": result.unchanged_count,
        "missing_count": result.missing_count,
        "dry_run": result.dry_run,
    }


def _bundle_comparison_record(comparison: BundleComparison) -> dict[str, Any]:
    return {
        "from_run": comparison.from_run,
        "to_run": comparison.to_run,
        "summary": comparison.summary,
        "items": list(comparison.items),
    }


def _format_seconds(value: float | None) -> str:
    seconds = value if value is not None else 0.0
    return f"{seconds:.2f}s"


def _format_duration(value: float | None) -> str:
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
