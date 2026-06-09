from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

from ..changelog import ChangelogRun
from ..fetch_manifest import fetch_result_has_warning, fetch_result_status
from ..models import EndpointSpec, FetchProgressEvent, FetchRunResult
from ..store import IngestResult
from .logs import format_duration, format_seconds

_COUNT_MISMATCH_RE = re.compile(
    r"Fetched (?P<fetched>\d+) items for '(?P<endpoint>[^']+)' "
    r"but count preflight expected (?P<expected>\d+)\."
)
_EARLY_COUNT_MISMATCH_RE = re.compile(
    r"Data pagination ended early for '(?P<endpoint>[^']+)': .* "
    r"after fetching (?P<fetched>\d+) of expected (?P<expected>\d+)\."
)


def print_human_fetch_run_header(
    *,
    mode: str,
    run_id: str,
    raw_dir: Path,
    selected_count: int,
    delta_state_file: Path,
    overlap_days: int,
    overlap_minutes: int,
    modified_since: str | None,
) -> None:
    print("Fetch run", file=sys.stderr)
    print(
        f"run={run_id}  mode={mode}  endpoints={_fmt_int(selected_count)}",
        file=sys.stderr,
    )
    print(f"raw={raw_dir}", file=sys.stderr)
    if mode == "delta":
        overlap = _format_overlap(overlap_days, overlap_minutes)
        print(
            f"delta_state={delta_state_file}  overlap={overlap}",
            file=sys.stderr,
        )
    elif modified_since is not None:
        print(f"modified_since={modified_since}", file=sys.stderr)
    print(file=sys.stderr)


def write_progress_line(event: FetchProgressEvent) -> None:
    if event.kind == "endpoint_start":
        expected = event.expected_count if event.expected_count is not None else "unknown"
        pieces = [
            f"expected={_fmt_int(expected)}",
            f"limit={_fmt_int(event.limit)}",
            f"skip={_fmt_int(event.start_skip)}",
            f"retries={_fmt_int(event.retries_used)}",
        ]
        if event.delta_floor is not None:
            pieces.append(f"delta_floor={event.delta_floor}")
        elif event.modified_since is not None:
            pieces.append(f"modified_since={event.modified_since}")
        pieces.append(f"elapsed={format_duration(event.elapsed_seconds)}")
        print(f"[{event.endpoint}] START  {'  '.join(pieces)}", file=sys.stderr)
        return
    if event.kind == "page_fetched":
        page_label = _fmt_int(event.page_index)
        if event.expected_pages is not None:
            page_label = f"{page_label}/{_fmt_int(event.expected_pages)}"
        line = (
            f"[{event.endpoint}] page {page_label}: page_items={_fmt_int(event.page_items)} "
            f"total_items={_fmt_int(event.items_fetched)} skip={_fmt_int(event.skip)} "
            f"next_skip={_fmt_int(event.next_skip)} elapsed={format_seconds(event.elapsed_seconds)}"
        )
        if event.percent_complete is not None:
            line += f" progress={event.percent_complete:.1f}%"
        if event.rolling_avg_seconds is not None:
            line += f" avg_page={format_duration(event.rolling_avg_seconds)}"
        if event.estimated_remaining_seconds is not None:
            line += f" eta={format_duration(event.estimated_remaining_seconds)}"
        print(line, file=sys.stderr)
        return
    if event.kind == "warning":
        print(f"[{event.endpoint}] WARN   {event.message}", file=sys.stderr)
        return
    if event.kind == "endpoint_finish":
        status = "EMPTY" if event.expected_count == 0 and event.items_fetched == 0 else "DONE"
        fetched = _fmt_int(event.items_fetched)
        expected = _fmt_int(event.expected_count)
        pieces = [
            f"items={fetched}/{expected}",
            f"pages={_fmt_int(event.pages_fetched)}",
            f"retries={_fmt_int(event.retries_used)}",
            f"warnings={_fmt_int(event.warnings_count)}",
        ]
        if event.resumed:
            pieces.append("resumed=true")
            pieces.append(f"start_skip={_fmt_int(event.start_skip)}")
        pieces.append(f"elapsed={format_duration(event.elapsed_seconds)}")
        print(f"[{event.endpoint}] {status:<5}  {'  '.join(pieces)}", file=sys.stderr)


def print_human_fetch_summary(
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
    log_path: Path | None = None,
) -> None:
    title = "Fetch complete"
    if failures or pipeline_error:
        title = "Fetch finished with failures"
    elif any(fetch_result_has_warning(result) for result in results):
        title = "Fetch complete with warnings"
    print(title)
    print()
    print(f"Mode: {mode}")
    print(f"Run:  {run_id}")
    print(f"Raw:  {raw_dir}")
    print()
    print("Summary")
    warn_count = sum(1 for result in results if _result_status(result) == "warn")
    ok_count = len(results) - warn_count
    print(
        f"Endpoints: {ok_count} ok, {warn_count} warn, {len(failures)} failed, "
        f"{selected_count} total"
    )
    print(f"Records:   {_fmt_int(sum(result.items_fetched for result in results))} fetched")
    print(f"Pages:     {_fmt_int(sum(result.pages_fetched for result in results))} fetched")
    print(f"Time:      {format_duration(duration_seconds)}")
    print(f"Retries:   {_fmt_int(sum(result.retries_used for result in results))}")
    table_rows = _fetch_summary_rows(results, failures)
    if table_rows:
        endpoint_width = max(len("Endpoint"), *(len(row["endpoint"]) for row in table_rows))
        records_width = max(len("Records"), *(len(row["records"]) for row in table_rows))
        expected_width = max(len("Expected"), *(len(row["expected"]) for row in table_rows))
        count_diff_width = max(len("Count Diff"), *(len(row["count_diff"]) for row in table_rows))
        count_diff_pct_width = max(
            len("Diff %"), *(len(row["count_diff_pct"]) for row in table_rows)
        )
        pages_width = max(len("Pages"), *(len(row["pages"]) for row in table_rows))
        retries_width = max(len("Retries"), *(len(row["retries"]) for row in table_rows))
        time_width = max(len("Time"), *(len(row["elapsed"]) for row in table_rows))
        warnings_width = max(len("Warnings"), *(len(row["warnings"]) for row in table_rows))
        validation_width = max(len("Validation"), *(len(row["validation"]) for row in table_rows))
        header = (
            f"{'Endpoint':<{endpoint_width}}  {'Status':<6}  "
            f"{'Records':>{records_width}}  {'Expected':>{expected_width}}  "
            f"{'Count Diff':>{count_diff_width}}  {'Diff %':>{count_diff_pct_width}}  "
            f"{'Pages':>{pages_width}}  {'Retries':>{retries_width}}  "
            f"{'Warnings':>{warnings_width}}  {'Validation':<{validation_width}}  "
            f"{'Time':>{time_width}}"
        )
        print()
        print(header)
        print("-" * len(header))
        for row in table_rows:
            print(
                f"{row['endpoint']:<{endpoint_width}}  {row['status']:<6}  "
                f"{row['records']:>{records_width}}  {row['expected']:>{expected_width}}  "
                f"{row['count_diff']:>{count_diff_width}}  "
                f"{row['count_diff_pct']:>{count_diff_pct_width}}  "
                f"{row['pages']:>{pages_width}}  {row['retries']:>{retries_width}}  "
                f"{row['warnings']:>{warnings_width}}  "
                f"{row['validation']:<{validation_width}}  "
                f"{row['elapsed']:>{time_width}}"
            )
    if ingest_result is not None:
        print()
        print("Ingest")
        print(
            f"Files:     {_fmt_int(ingest_result.applied_files)} applied, "
            f"{_fmt_int(ingest_result.skipped_files)} skipped"
        )
        print(f"Records:   {_fmt_int(ingest_result.records_read)} read")
        print(f"Upserts:   {_fmt_int(ingest_result.records_upserted)}")
        print(f"Deletes:   {_fmt_int(ingest_result.records_deleted)}")
        print(f"Hard del:  {_fmt_int(ingest_result.records_hard_deleted)}")
        if ingest_result.invalid_records:
            print(f"Invalid:   {_fmt_int(ingest_result.invalid_records)}")
    if changelog_run is not None:
        print()
        print("Changelog")
        print(f"Events:    {_fmt_int(changelog_run.event_count)}")
        print(f"Scoped:    {_fmt_int(changelog_run.scoped_record_count)}")
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
            print(f"- {endpoint}")
            for line in textwrap.wrap(message, width=88):
                print(f"  {line}")
    if (failures or pipeline_error) and log_path is not None:
        print()
        print(f"Log: {log_path}")


def print_json_fetch_records(
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
                    "status": fetch_result_status(result),
                    "items_fetched": result.items_fetched,
                    "pages_fetched": result.pages_fetched,
                    "expected_count": result.expected_count,
                    "retries_used": result.retries_used,
                    "warnings": result.warnings,
                    "warnings_count": len(result.warnings),
                    "count_validation": result.count_validation_status,
                    "count_validation_reason": result.count_validation_reason,
                    "id_validation": result.id_validation_status,
                    "id_validation_checked_items": result.id_validation_checked_items,
                    "id_validation_unique_ids": result.id_validation_unique_ids,
                    "output_file": str(result.output_file) if result.output_file_created else None,
                    "output_file_created": result.output_file_created,
                }
            )
        )
    for endpoint, message in failures:
        print(json.dumps({"endpoint": endpoint, "status": "failed", "error": message}))
    endpoints_warn = sum(1 for result in results if fetch_result_has_warning(result))
    print(
        json.dumps(
            {
                "record_type": "pipeline_summary",
                "status": _pipeline_summary_status(
                    results=results,
                    failures=failures,
                    pipeline_error=pipeline_error,
                ),
                "endpoints_ok": len(results) - endpoints_warn,
                "endpoints_warn": endpoints_warn,
                "endpoints_failed": len(failures),
                "manifest": str(manifest_path),
                "ingest": _ingest_record(ingest_result),
                "changelog": _changelog_record(changelog_run, changelog_skipped),
                "pipeline_error": pipeline_error,
            }
        )
    )


def print_delta_dry_run(
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


def _fmt_int(value: int | str | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _format_overlap(days: int, minutes: int) -> str:
    pieces: list[str] = []
    if days:
        pieces.append(f"{days}d")
    if minutes:
        pieces.append(f"{minutes}m")
    return " ".join(pieces) if pieces else "0m"


def _validation_status(result: FetchRunResult) -> str:
    count_status = result.count_validation_status
    id_status = result.id_validation_status
    if count_status == "passed" and id_status == "passed":
        return "ok"
    if count_status == "warning" and id_status == "passed":
        return "warn"
    failed = [
        f"{label}_failed"
        for label, status in (("count", count_status), ("id", id_status))
        if status not in {"passed", "warning"}
    ]
    return "+".join(failed) if failed else "unknown"


def _fetch_summary_rows(
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = [
        {
            "endpoint": result.endpoint,
            "status": _result_status(result),
            "records": _fmt_int(result.items_fetched),
            "expected": _fmt_int(result.expected_count),
            "count_diff": _count_diff(result.items_fetched, result.expected_count),
            "count_diff_pct": _count_diff_pct(result.items_fetched, result.expected_count),
            "pages": _fmt_int(result.pages_fetched),
            "retries": _fmt_int(result.retries_used),
            "warnings": _fmt_int(len(result.warnings)),
            "validation": _validation_status(result),
            "elapsed": format_duration(result.duration_seconds),
        }
        for result in results
    ]
    rows.extend(_failure_summary_row(endpoint, message) for endpoint, message in failures)
    return rows


def _failure_summary_row(endpoint: str, message: str) -> dict[str, str]:
    records = "unknown"
    expected = "unknown"
    count_diff = "unknown"
    count_diff_pct = "unknown"
    match = _count_mismatch_match(message)
    if match:
        fetched_count = int(match.group("fetched"))
        expected_count = int(match.group("expected"))
        records = _fmt_int(fetched_count)
        expected = _fmt_int(expected_count)
        count_diff = _count_diff(fetched_count, expected_count)
        count_diff_pct = _count_diff_pct(fetched_count, expected_count)
    return {
        "endpoint": endpoint,
        "status": "failed",
        "records": records,
        "expected": expected,
        "count_diff": count_diff,
        "count_diff_pct": count_diff_pct,
        "pages": "unknown",
        "retries": "unknown",
        "warnings": "1",
        "validation": "failed",
        "elapsed": "unknown",
    }


def _count_mismatch_match(message: str) -> re.Match[str] | None:
    return _COUNT_MISMATCH_RE.search(message) or _EARLY_COUNT_MISMATCH_RE.search(message)


def _result_status(result: FetchRunResult) -> str:
    if _validation_status(result) == "warn" or result.warnings:
        return "warn"
    return "ok"


def _pipeline_summary_status(
    *,
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    pipeline_error: str | None,
) -> str:
    if pipeline_error or (failures and not results):
        return "failed"
    if failures:
        return "partial"
    if any(fetch_result_has_warning(result) for result in results):
        return "warn"
    return "ok"


def _count_diff(fetched_count: int, expected_count: int) -> str:
    diff = fetched_count - expected_count
    if diff == 0:
        return "0"
    direction = "over" if diff > 0 else "under"
    return f"{diff:+,} {direction}"


def _count_diff_pct(fetched_count: int, expected_count: int) -> str:
    diff = fetched_count - expected_count
    if diff == 0:
        return "-"
    if expected_count == 0:
        return "unknown"
    return f"{(diff / expected_count) * 100:+.3f}%"


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
