from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..changelog import ChangelogRun, record_changelog
from ..store import IngestResult

PipelineProgressCallback = Callable[[str], None]


def run_changelog_after_ingest(
    db_path: Path,
    ingest_result: IngestResult,
    *,
    progress: PipelineProgressCallback | None = None,
) -> tuple[ChangelogRun | None, str | None]:
    changed_endpoints = set(ingest_result.changed_record_ids_by_endpoint)
    if not changed_endpoints:
        return None, "no current-record changes"
    return (
        record_changelog(
            db_path,
            endpoints=changed_endpoints,
            record_ids_by_endpoint={
                endpoint: set(record_ids)
                for endpoint, record_ids in ingest_result.upserted_record_ids_by_endpoint.items()
            },
            deleted_record_ids_by_endpoint={
                endpoint: set(record_ids)
                for endpoint, record_ids in ingest_result.deleted_record_ids_by_endpoint.items()
            },
            deleted_record_delete_types_by_endpoint=(
                ingest_result.deleted_record_delete_types_by_endpoint
            ),
            progress=progress,
        ),
        None,
    )


__all__ = ["PipelineProgressCallback", "run_changelog_after_ingest"]
