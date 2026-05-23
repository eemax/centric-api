from __future__ import annotations

import json
import sys
from typing import Any

from ..download import DownloadRunResult
from .logs import format_seconds


def write_download_progress_line(event: dict[str, Any]) -> None:
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
            f"elapsed={format_seconds(event.get('elapsed_seconds'))}"
        )
        if event.get("bytes") is not None:
            line += f" bytes={event.get('bytes')}"
        if event.get("error"):
            line += f" error={json.dumps(event.get('error'))}"
        print(line, file=sys.stderr)


def write_json_download_progress(event: dict[str, Any]) -> None:
    print(json.dumps({"record_type": event.get("event"), **event}, default=str))


def print_human_download_summary(result: DownloadRunResult) -> None:
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


def download_record(result: DownloadRunResult) -> dict[str, Any]:
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
