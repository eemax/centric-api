from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..changelog import ChangelogRun
from ..models import EndpointSpec, FetchProgressEvent, FetchRunResult
from ..store import IngestResult
from .logs import format_duration, format_seconds


def write_progress_line(event: FetchProgressEvent) -> None:
    if event.kind == "endpoint_start":
        expected = event.expected_count if event.expected_count is not None else "unknown"
        print(
            f"[{event.endpoint}] start: skip={event.start_skip} limit={event.limit} "
            f"expected={expected} retries={event.retries_used} "
            f"elapsed={format_seconds(event.elapsed_seconds)}",
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
            f"elapsed={format_seconds(event.elapsed_seconds)}"
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
        print(f"[{event.endpoint}] warning: {event.message}", file=sys.stderr)
        return
    if event.kind == "endpoint_finish":
        print(
            f"[{event.endpoint}] finish: pages={event.pages_fetched} "
            f"items={event.items_fetched} retries={event.retries_used} "
            f"warnings={event.warnings_count} elapsed={format_seconds(event.elapsed_seconds)}",
            file=sys.stderr,
        )


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
    print(f"Time:      {format_duration(duration_seconds)}")
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
