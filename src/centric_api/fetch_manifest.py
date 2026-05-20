from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import EndpointSpec, FetchRunResult


def write_run_manifest(
    *,
    output_dir: Path,
    run_id: str,
    mode: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    selected_specs: list[EndpointSpec],
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    endpoint_records: list[dict[str, Any]],
    modified_since: str | None,
    utc_iso,
) -> Path:
    status = (
        "OK" if not failures else ("FAILED" if len(failures) == len(selected_specs) else "PARTIAL")
    )
    manifest = {
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "started_at": utc_iso(run_started_at),
        "finished_at": utc_iso(run_finished_at),
        "duration_seconds": round((run_finished_at - run_started_at).total_seconds(), 3),
        "output_dir": str(output_dir),
        "selected_endpoints": [spec.name for spec in selected_specs],
        "endpoints_total": len(selected_specs),
        "endpoints_succeeded": len(results),
        "endpoints_failed": len(failures),
        "total_items": sum(result.items_fetched for result in results),
        "modified_since": modified_since,
        "failures": [{"endpoint": endpoint, "error": message} for endpoint, message in failures],
        "endpoints": {record["endpoint"]: record for record in endpoint_records},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    temp_path = output_dir / ".manifest.json.tmp"
    temp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(manifest_path)
    return manifest_path


def endpoint_manifest_record(
    result: FetchRunResult,
    *,
    mode: str,
    status: str,
    attempt_start: str,
    attempt_end: str,
    delta_floor: str | None,
    modified_since: str | None,
) -> dict[str, Any]:
    return {
        "endpoint": result.endpoint,
        "file": result.output_file.name if result.output_file_created else None,
        "output_file_created": result.output_file_created,
        "mode": mode,
        "status": status,
        "is_delta": mode == "delta",
        "delta_floor": delta_floor,
        "modified_since": modified_since,
        "attempt_start": attempt_start,
        "attempt_end": attempt_end,
        "items_fetched": result.items_fetched,
        "pages_fetched": result.pages_fetched,
        "expected_count": result.expected_count,
        "retries_used": result.retries_used,
        "warnings": result.warnings,
        "error": None,
    }
