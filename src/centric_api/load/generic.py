from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..auth import AuthContext
from ..config import ConfigError, runtime_path
from ..load_config import LoadConfig, LoadJob
from .artifacts import (
    _emit_progress,
    _execute_requests,
    _has_row_issues,
    _write_requests,
    _write_responses,
    _write_review_workbook,
    _write_summary,
)
from .excel import (
    _include_retry_row,
    _iter_data_rows,
    _map_headers,
    _retry_status_index,
    _select_sheet,
)
from .models import (
    LOAD_RUNS_DIR,
    REVIEW_WORKBOOK_NAME,
    LoadIssue,
    LoadMaterialized,
    LoadProgressCallback,
    LoadRequest,
    LoadResponse,
    LoadRunResult,
)
from .references import _build_reference_indexes, _build_value_set_indexes, _row_values
from .utils import _is_blank, _run_id, _utc_iso


def materialize_load(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None = None,
    limit: int | None = None,
    mode: str = "check",
    retry_statuses: set[str] | None = None,
    progress_callback: LoadProgressCallback | None = None,
) -> LoadMaterialized:
    workbook_path = workbook_path.expanduser()
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        worksheet = _select_sheet(workbook, sheet)
        _emit_progress(
            progress_callback,
            {
                "event": "load_planning",
                "job": job.name,
                "mode": mode,
                "workbook": str(workbook_path),
                "sheet": worksheet.title,
            },
        )
        header_map, header_issues, header_stats = _map_headers(job, worksheet)
        retry_status_index = _retry_status_index(
            worksheet,
            job.input.header_row,
            retry_statuses=retry_statuses,
        )
        _emit_progress(
            progress_callback,
            {
                "event": "load_headers",
                "matched": header_stats["matched"],
                "columns": len(job.columns),
                "required_matched": header_stats["required_matched"],
                "required": header_stats["required"],
                "aliases": header_stats["aliases"],
                "issues": len(header_issues),
            },
        )
        reference_indexes = _build_reference_indexes(
            db_path,
            job,
            progress_callback=progress_callback,
        )
        value_set_indexes = _build_value_set_indexes(
            job,
            progress_callback=progress_callback,
        )
        requests: list[LoadRequest] = []
        issues: list[LoadIssue] = list(header_issues)
        rows_scanned = 0
        error_rows = 0
        if not header_issues:
            for row_number, row_values in _iter_data_rows(worksheet, job.input.header_row):
                if not _include_retry_row(row_values, retry_status_index, retry_statuses):
                    continue
                if limit is not None and rows_scanned >= limit:
                    break
                rows_scanned += 1
                values, row_issues = _row_values(
                    job,
                    row_number=row_number,
                    row_values=row_values,
                    header_map=header_map,
                    reference_indexes=reference_indexes,
                    value_set_indexes=value_set_indexes,
                )
                if row_issues:
                    error_rows += 1
                    issues.extend(row_issues)
                    continue
                path = _request_path(job, values, row_number=row_number)
                if isinstance(path, LoadIssue):
                    error_rows += 1
                    issues.append(path)
                    continue
                requests.append(
                    LoadRequest(
                        row=row_number,
                        method=job.method,
                        path=path,
                        body=_request_body(job, values),
                    )
                )
        _emit_progress(
            progress_callback,
            {
                "event": "load_validate",
                "scanned": rows_scanned,
                "valid": len(requests),
                "errors": error_rows,
            },
        )
        return LoadMaterialized(
            job_name=job.name,
            title=job.title,
            workbook_path=workbook_path,
            sheet=worksheet.title,
            header_row=job.input.header_row,
            rows_scanned=rows_scanned,
            valid_rows=len(requests),
            error_rows=error_rows,
            issues=tuple(issues),
            requests=tuple(requests),
        )
    finally:
        workbook.close()


def run_load(
    db_path: Path,
    config: LoadConfig,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    dry_run: bool,
    yes: bool,
    retry_statuses: set[str] | None = None,
    materialized: LoadMaterialized | None = None,
    auth_ctx: AuthContext | None = None,
    progress_callback: LoadProgressCallback | None = None,
) -> LoadRunResult:
    mode = (
        "retry-dry-run"
        if retry_statuses and dry_run
        else ("retry" if retry_statuses else ("dry-run" if dry_run else "run"))
    )
    if materialized is None:
        materialized = materialize_load(
            db_path,
            job,
            workbook_path,
            sheet=sheet,
            limit=limit,
            mode=mode,
            retry_statuses=retry_statuses,
            progress_callback=progress_callback,
        )
    if not dry_run and not yes:
        raise ConfigError("Non-dry-run load requires --yes.")
    started_at = _utc_iso()
    run_id = _run_id(job.name)
    run_dir = runtime_path(LOAD_RUNS_DIR / run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_requests(run_dir / "requests.jsonl", materialized.requests)
    _emit_progress(
        progress_callback,
        {
            "event": "load_artifacts",
            "run_dir": str(run_dir),
            "requests": len(materialized.requests),
        },
    )
    responses: tuple[LoadResponse, ...] = ()
    if not dry_run and materialized.requests:
        if auth_ctx is None:
            raise ConfigError("Load run requires an auth context.")
        responses = tuple(
            _execute_requests(
                auth_ctx,
                materialized.requests,
                progress_callback=progress_callback,
            )
        )
        _write_responses(run_dir / "responses.jsonl", responses)
    finished_at = _utc_iso()
    review_path = None
    if responses or _has_row_issues(materialized.issues):
        review_path = _write_review_workbook(
            materialized,
            responses=responses,
            run_id=run_id,
            processed_at=finished_at,
            output_path=run_dir / REVIEW_WORKBOOK_NAME,
        )
    result = LoadRunResult(
        run_id=run_id,
        job_name=job.name,
        title=job.title,
        mode=mode,
        dry_run=dry_run,
        workbook_path=materialized.workbook_path,
        sheet=materialized.sheet,
        rows_scanned=materialized.rows_scanned,
        valid_rows=materialized.valid_rows,
        error_rows=materialized.error_rows,
        request_count=len(materialized.requests),
        success_count=sum(1 for response in responses if response.ok),
        failure_count=sum(1 for response in responses if not response.ok),
        issues=materialized.issues,
        requests=materialized.requests,
        responses=responses,
        run_dir=run_dir,
        review_path=review_path,
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_summary(run_dir / "summary.json", result, config)
    return result


def _request_path(job: LoadJob, values: dict[str, Any], *, row_number: int) -> str | LoadIssue:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = values.get(key)
        return "" if _is_blank(value) else str(value).strip()

    path = re.sub(r"{([A-Za-z_][A-Za-z0-9_]*)}", replace, job.path)
    if "{}" in path or re.search(r"{[A-Za-z_][A-Za-z0-9_]*}", path):
        return LoadIssue(
            row=row_number,
            code="path_template_unresolved",
            message=f"Request path could not be resolved from template {job.path!r}.",
        )
    if "//" in path:
        return LoadIssue(
            row=row_number,
            code="path_template_blank",
            message=f"Request path has a blank template value: {job.path!r}.",
        )
    return path


def _request_body(job: LoadJob, values: dict[str, Any]) -> Any:
    if isinstance(job.body, str):
        return values.get(job.body)
    body: dict[str, Any] = {}
    for target, source in job.body.items():
        value = values.get(source)
        if _is_blank(value):
            continue
        body[target] = value
    return body
