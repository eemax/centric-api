from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..changelog import ChangelogRun
from ..models import EndpointSpec, FetchProgressEvent, FetchRunResult
from ..store import IngestResult
from .logs import format_duration, format_seconds


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
    print(title)
    print()
    print(f"Mode: {mode}")
    print(f"Run:  {run_id}")
    print(f"Raw:  {raw_dir}")
    print()
    print("Summary")
    print(f"Endpoints: {len(results)} ok, {len(failures)} failed, {selected_count} total")
    print(f"Records:   {_fmt_int(sum(result.items_fetched for result in results))} fetched")
    print(f"Pages:     {_fmt_int(sum(result.pages_fetched for result in results))} fetched")
    print(f"Time:      {format_duration(duration_seconds)}")
    print(f"Retries:   {_fmt_int(sum(result.retries_used for result in results))}")
    if results:
        endpoint_width = max(len("Endpoint"), *(len(result.endpoint) for result in results))
        rows = [
            (
                result.endpoint,
                "ok",
                _fmt_int(result.items_fetched),
                _fmt_int(result.expected_count),
                _fmt_int(result.pages_fetched),
                _fmt_int(result.retries_used),
                format_duration(result.duration_seconds),
            )
            for result in results
        ]
        records_width = max(len("Records"), *(len(row[2]) for row in rows))
        expected_width = max(len("Expected"), *(len(row[3]) for row in rows))
        pages_width = max(len("Pages"), *(len(row[4]) for row in rows))
        retries_width = max(len("Retries"), *(len(row[5]) for row in rows))
        time_width = max(len("Time"), *(len(row[6]) for row in rows))
        warnings_width = max(
            len("Warnings"),
            *(len(_fmt_int(len(result.warnings))) for result in results),
        )
        validations = [_validation_status(result) for result in results]
        validation_width = max(len("Validation"), *(len(value) for value in validations))
        header = (
            f"{'Endpoint':<{endpoint_width}}  {'Status':<6}  "
            f"{'Records':>{records_width}}  {'Expected':>{expected_width}}  "
            f"{'Pages':>{pages_width}}  {'Retries':>{retries_width}}  "
            f"{'Warnings':>{warnings_width}}  {'Validation':<{validation_width}}  "
            f"{'Time':>{time_width}}"
        )
        print()
        print(header)
        print("-" * len(header))
        for row, result, validation in zip(rows, results, validations, strict=True):
            endpoint, status, records, expected, pages, retries, elapsed = row
            print(
                f"{endpoint:<{endpoint_width}}  {status:<6}  "
                f"{records:>{records_width}}  {expected:>{expected_width}}  "
                f"{pages:>{pages_width}}  {retries:>{retries_width}}  "
                f"{_fmt_int(len(result.warnings)):>{warnings_width}}  "
                f"{validation:<{validation_width}}  "
                f"{elapsed:>{time_width}}"
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
            print(f"- {endpoint}: {message}")
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
    failed = [
        f"{label}_failed"
        for label, status in (("count", count_status), ("id", id_status))
        if status != "passed"
    ]
    return "+".join(failed) if failed else "unknown"


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
