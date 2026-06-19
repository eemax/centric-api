from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..changelog import ChangelogRun
from ..defaults import db_path as resolve_db_path
from ..fetch_delta_state import update_delta_state_for_endpoint, write_delta_state
from ..models import EndpointSpec, FetcherConfig, FetchRunResult
from ..raw_lifecycle import (
    failed_run_dir,
    promote_run_dir,
    write_completed_marker,
    write_failed_marker,
)
from ..rendering.logs import LogCallback, format_duration
from ..store import IngestResult
from ._fetch_runtime import remap_result_output_files
from .common import utc_iso, utc_now

PipelineProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class DeltaStateUpdate:
    endpoint_name: str
    status: str
    attempt_start: str
    attempt_end: str


@dataclass(frozen=True)
class FetchPipelineResult:
    manifest_path: Path
    ingest_result: IngestResult | None
    changelog_run: ChangelogRun | None
    changelog_skipped: str | None
    pipeline_error: str | None


def run_post_fetch_pipeline(
    *,
    db_arg: str | None,
    schema_arg: str | None,
    fetcher_cfg: FetcherConfig,
    raw_root_dir: Path,
    run_id: str,
    mode: str,
    run_started_dt: datetime,
    selected_specs: list[EndpointSpec],
    results: list[FetchRunResult],
    failures: list[tuple[str, str]],
    endpoint_records: list[dict[str, Any]],
    modified_since: str | None,
    pipeline_progress: PipelineProgressCallback | None,
    log_callback: LogCallback | None,
    delta_state: dict[str, Any],
    delta_state_file: Path,
    pending_successful_delta_updates: list[DeltaStateUpdate],
    raw_run_failed: bool,
    write_run_manifest_func: Callable[..., Path],
    load_endpoint_schemas_func: Callable[..., Any],
    ingest_raw_dir_func: Callable[..., IngestResult],
    run_changelog_after_ingest_func: Callable[..., tuple[ChangelogRun | None, str | None]],
) -> FetchPipelineResult:
    pipeline_started = time.time()
    _emit_pipeline_progress(pipeline_progress, "")
    _emit_pipeline_progress(pipeline_progress, "Pipeline")
    _emit_pipeline_progress(pipeline_progress, "manifest=writing")
    manifest_path = fetcher_cfg.output_dir / "manifest.json"
    ingest_result: IngestResult | None = None
    changelog_run: ChangelogRun | None = None
    changelog_skipped: str | None = None
    pipeline_error: str | None = None
    ingest_status = "skipped"
    changelog_status = "skipped"
    try:
        manifest_path = write_run_manifest_func(
            output_dir=fetcher_cfg.output_dir,
            run_id=run_id,
            mode=mode,
            run_started_at=run_started_dt,
            run_finished_at=utc_now(),
            selected_specs=selected_specs,
            results=results,
            failures=failures,
            endpoint_records=endpoint_records,
            modified_since=modified_since,
            utc_iso=utc_iso,
        )
    except Exception as exc:
        pipeline_error = f"manifest failed: {exc}"
        _emit_pipeline_progress(pipeline_progress, f"manifest=failed error={exc}")
        if not raw_run_failed:
            fetcher_cfg.output_dir = promote_run_dir(
                fetcher_cfg.output_dir,
                failed_run_dir(raw_root_dir, run_id),
            )
            remap_result_output_files(results, fetcher_cfg.output_dir)
            raw_run_failed = True
        write_failed_marker(
            fetcher_cfg.output_dir,
            run_id=run_id,
            mode=mode,
            started_at=utc_iso(run_started_dt),
            failed_at=utc_iso(),
            manifest_path=None,
            reason=pipeline_error,
            failures=[{"endpoint": endpoint, "error": message} for endpoint, message in failures],
        )
        if log_callback:
            log_callback(
                {
                    "level": "summary",
                    "event": "manifest_failed",
                    "error": str(exc),
                }
            )
    else:
        _emit_pipeline_progress(pipeline_progress, f"manifest=ok path={manifest_path}")
        if raw_run_failed:
            write_failed_marker(
                fetcher_cfg.output_dir,
                run_id=run_id,
                mode=mode,
                started_at=utc_iso(run_started_dt),
                failed_at=utc_iso(),
                manifest_path=manifest_path,
                reason="endpoint fetch failures",
                failures=[
                    {"endpoint": endpoint, "error": message} for endpoint, message in failures
                ],
            )
        else:
            write_completed_marker(
                fetcher_cfg.output_dir,
                run_id=run_id,
                mode=mode,
                started_at=utc_iso(run_started_dt),
                completed_at=utc_iso(),
                manifest_path=manifest_path,
            )

    if results and not raw_run_failed and pipeline_error is None:
        db_path = resolve_db_path(db_arg)
        _emit_pipeline_progress(pipeline_progress, "ingest=running")
        try:
            schema_path = Path(schema_arg).expanduser() if schema_arg else None
            schemas = load_endpoint_schemas_func(schema_path)
            ingest_result = ingest_raw_dir_func(fetcher_cfg.output_dir, db_path, schemas=schemas)
        except Exception as exc:
            ingest_status = "failed"
            pipeline_error = f"ingest failed: {exc}"
            _emit_pipeline_progress(pipeline_progress, f"ingest=failed error={exc}")
            if log_callback:
                log_callback(
                    {
                        "level": "summary",
                        "event": "ingest_failed",
                        "error": str(exc),
                    }
                )
        else:
            ingest_status = "ok"
            _emit_pipeline_progress(
                pipeline_progress,
                (
                    f"ingest=ok records_read={_fmt_cli_int(ingest_result.records_read)} "
                    f"upserts={_fmt_cli_int(ingest_result.records_upserted)} "
                    f"deletes={_fmt_cli_int(ingest_result.records_deleted)}"
                ),
            )
            if log_callback:
                log_callback(
                    {
                        "level": "summary",
                        "event": "ingest_ok",
                        "applied_files": ingest_result.applied_files,
                        "skipped_files": ingest_result.skipped_files,
                        "records_read": ingest_result.records_read,
                        "upserts": ingest_result.records_upserted,
                        "deletes": ingest_result.records_deleted,
                        "hard_deletes": ingest_result.records_hard_deleted,
                        "invalid": ingest_result.invalid_records,
                    }
                )
        if ingest_result is not None:
            _emit_pipeline_progress(pipeline_progress, "changelog=running")
            changelog_started = time.time()
            try:
                changelog_run, changelog_skipped = run_changelog_after_ingest_func(
                    db_path,
                    ingest_result,
                    progress=_indented_pipeline_progress(pipeline_progress),
                )
            except Exception as exc:
                changelog_status = "failed"
                pipeline_error = f"changelog failed after ingest: {exc}"
                _emit_pipeline_progress(
                    pipeline_progress,
                    (
                        "changelog=failed "
                        f"elapsed={format_duration(time.time() - changelog_started)} "
                        f"error={exc}"
                    ),
                )
                if log_callback:
                    log_callback(
                        {
                            "level": "summary",
                            "event": "changelog_failed",
                            "error": str(exc),
                        }
                    )
            else:
                if changelog_skipped:
                    changelog_status = "skipped"
                    _emit_pipeline_progress(
                        pipeline_progress,
                        (
                            f"changelog=skipped "
                            f"elapsed={format_duration(time.time() - changelog_started)} "
                            f"reason={changelog_skipped}"
                        ),
                    )
                elif changelog_run is not None:
                    changelog_status = "ok"
                    _emit_pipeline_progress(
                        pipeline_progress,
                        (
                            f"changelog=ok events={_fmt_cli_int(changelog_run.event_count)} "
                            f"scoped={_fmt_cli_int(changelog_run.scoped_record_count)} "
                            f"elapsed={format_duration(time.time() - changelog_started)}"
                        ),
                    )
                if log_callback:
                    if changelog_run is not None:
                        log_callback(
                            {
                                "level": "summary",
                                "event": "changelog_ok",
                                "events": changelog_run.event_count,
                                "scoped": changelog_run.scoped_record_count,
                                "run_id": changelog_run.run_id,
                            }
                        )
                    elif changelog_skipped:
                        log_callback(
                            {
                                "level": "summary",
                                "event": "changelog_skipped",
                                "reason": changelog_skipped,
                            }
                        )
    else:
        skipped_reason = "no successful endpoint fetches"
        if raw_run_failed:
            skipped_reason = "endpoint fetch failures"
        if pipeline_error is not None:
            skipped_reason = pipeline_error
        _emit_pipeline_progress(
            pipeline_progress,
            f"ingest=skipped reason={skipped_reason}",
        )
        if log_callback:
            log_callback(
                {
                    "level": "summary",
                    "event": "ingest_skipped",
                    "reason": skipped_reason,
                }
            )

    if mode in {"delta", "full"}:
        _apply_successful_delta_updates(
            delta_state,
            delta_state_file=delta_state_file,
            updates=pending_successful_delta_updates,
            pipeline_error="endpoint fetch failures" if raw_run_failed else pipeline_error,
        )

    _emit_pipeline_progress(
        pipeline_progress,
        (
            f"pipeline=done ingest={ingest_status} changelog={changelog_status} "
            f"elapsed={format_duration(time.time() - pipeline_started)}"
        ),
    )
    return FetchPipelineResult(
        manifest_path=manifest_path,
        ingest_result=ingest_result,
        changelog_run=changelog_run,
        changelog_skipped=changelog_skipped,
        pipeline_error=pipeline_error,
    )


def _apply_successful_delta_updates(
    delta_state: dict[str, Any],
    *,
    delta_state_file: Path,
    updates: list[DeltaStateUpdate],
    pipeline_error: str | None,
) -> None:
    if not updates:
        return
    for update in updates:
        status = update.status if pipeline_error is None else "PIPELINE_FAILED"
        update_delta_state_for_endpoint(
            delta_state,
            endpoint_name=update.endpoint_name,
            status=status,
            attempt_start=update.attempt_start,
            attempt_end=update.attempt_end,
            error=pipeline_error,
        )
    write_delta_state(delta_state_file, delta_state)


def _emit_pipeline_progress(
    progress: PipelineProgressCallback | None,
    message: str,
) -> None:
    if progress is not None:
        progress(message)


def _indented_pipeline_progress(
    progress: PipelineProgressCallback | None,
) -> PipelineProgressCallback | None:
    if progress is None:
        return None

    def emit(message: str) -> None:
        progress(f"  {message}")

    return emit


def _fmt_cli_int(value: int) -> str:
    return f"{value:,}"
