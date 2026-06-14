from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from openpyxl import load_workbook

from ..auth import AuthContext
from ..config import ConfigError, runtime_path
from ..load_config import LoadConfig, LoadJob
from .artifacts import (
    _emit_progress,
    _execute_chained_load_request,
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
from .utils import _run_id, _utc_iso

ParsedRows = dict[str, Any]
RowFactory = Callable[[int, dict[str, Any]], Any]
RowValidator = Callable[[dict[str, Any], int], list[LoadIssue]]
RequireColumns = Callable[[LoadJob], None]
PlannedRequests = Callable[[Any], tuple[LoadRequest, ...]]
PlanRows = Callable[[ParsedRows, Callable[[LoadRequest], LoadResponse] | None], Any]
ErrorRowCount = Callable[[tuple[LoadIssue, ...] | list[LoadIssue]], int]


class ExecuteRows(Protocol):
    def __call__(
        self,
        auth_ctx: AuthContext,
        rows: Any,
        *,
        requests: list[LoadRequest],
        responses: list[LoadResponse],
        progress_callback: LoadProgressCallback | None,
    ) -> list[LoadIssue]: ...


def parse_workflow_rows(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    mode: str,
    retry_statuses: set[str] | None,
    progress_callback: LoadProgressCallback | None,
    require_columns: RequireColumns,
    row_factory: RowFactory,
    validate_values: RowValidator | None = None,
) -> ParsedRows:
    require_columns(job)
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
        rows: list[Any] = []
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
                if not row_issues and validate_values is not None:
                    row_issues.extend(validate_values(values, row_number))
                if row_issues:
                    error_rows += 1
                    issues.extend(row_issues)
                    continue
                rows.append(row_factory(row_number, values))
        return {
            "sheet": worksheet.title,
            "rows_scanned": rows_scanned,
            "error_rows": error_rows,
            "issues": issues,
            "rows": tuple(rows),
        }
    finally:
        workbook.close()


def materialize_chained_workflow(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    mode: str,
    retry_statuses: set[str] | None,
    progress_callback: LoadProgressCallback | None,
    parse_rows: Callable[..., ParsedRows],
    planned_requests: PlannedRequests,
) -> LoadMaterialized:
    parsed = parse_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
    )
    requests = planned_requests(parsed["rows"])
    return materialize_from_parsed(
        job,
        workbook_path,
        parsed,
        requests=requests,
        progress_callback=progress_callback,
    )


def run_chained_workflow(
    db_path: Path,
    config: LoadConfig,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    dry_run: bool,
    yes: bool,
    retry_statuses: set[str] | None,
    materialized: LoadMaterialized | None,
    auth_ctx: AuthContext | None,
    progress_callback: LoadProgressCallback | None,
    parse_rows: Callable[..., ParsedRows],
    planned_requests: PlannedRequests,
    execute_rows: ExecuteRows,
) -> LoadRunResult:
    if not dry_run and not yes:
        raise ConfigError("Non-dry-run load requires --yes.")
    mode = workflow_mode(dry_run=dry_run, retry_statuses=retry_statuses)
    parsed = parse_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
    )
    started_at = _utc_iso()
    run_id = _run_id(job.name)
    run_dir = runtime_path(LOAD_RUNS_DIR / run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    materialized = materialized or _materialized_without_progress(
        job,
        workbook_path,
        parsed,
        requests=planned_requests(parsed["rows"]),
    )
    requests = list(materialized.requests)
    responses: list[LoadResponse] = []
    issues = list(materialized.issues)
    workflow_issues: list[LoadIssue] = []
    if dry_run:
        _write_requests(run_dir / "requests.jsonl", tuple(requests))
    else:
        if parsed["rows"] and auth_ctx is None:
            raise ConfigError("Load run requires an auth context.")
        requests = []
        responses = []
        if parsed["rows"]:
            workflow_issues = execute_rows(
                auth_ctx,
                parsed["rows"],
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )
            issues.extend(workflow_issues)
        _write_requests(run_dir / "requests.jsonl", tuple(requests))
        _write_responses(run_dir / "responses.jsonl", tuple(responses))
    _emit_progress(
        progress_callback,
        {
            "event": "load_artifacts",
            "run_dir": str(run_dir),
            "requests": len(requests),
        },
    )

    finished_at = _utc_iso()
    materialized = _materialized_without_progress(
        job,
        workbook_path,
        parsed,
        valid_rows=len(parsed["rows"]),
        error_rows=int(parsed["error_rows"]) + len(workflow_issues if not dry_run else ()),
        issues=tuple(issues),
        requests=tuple(requests),
    )
    review_path = None
    if responses or _has_row_issues(materialized.issues):
        review_path = _write_review_workbook(
            materialized,
            responses=tuple(responses),
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
        workbook_path=Path(workbook_path).expanduser(),
        sheet=str(parsed["sheet"]),
        rows_scanned=int(parsed["rows_scanned"]),
        valid_rows=len(parsed["rows"]),
        error_rows=materialized.error_rows,
        request_count=len(requests),
        success_count=sum(1 for response in responses if response.ok),
        failure_count=sum(1 for response in responses if not response.ok),
        issues=tuple(issues),
        requests=tuple(requests),
        responses=tuple(responses),
        run_dir=run_dir,
        review_path=review_path,
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_summary(run_dir / "summary.json", result, config)
    return result


def materialize_planning_workflow(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    mode: str,
    retry_statuses: set[str] | None,
    progress_callback: LoadProgressCallback | None,
    parse_rows: Callable[..., ParsedRows],
    plan_rows: PlanRows,
    error_row_count: ErrorRowCount,
) -> LoadMaterialized:
    parsed = parse_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
    )
    plan = plan_rows(parsed, None)
    issues = (*parsed["issues"], *plan.issues)
    error_rows = error_row_count(issues)
    return materialize_from_parsed(
        job,
        workbook_path,
        parsed,
        valid_rows=max(len(parsed["rows"]) - error_rows, 0),
        error_rows=error_rows,
        issues=issues,
        requests=tuple(plan.requests),
        progress_callback=progress_callback,
    )


def run_planning_workflow(
    db_path: Path,
    config: LoadConfig,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    dry_run: bool,
    yes: bool,
    retry_statuses: set[str] | None,
    materialized: LoadMaterialized | None,
    auth_ctx: AuthContext | None,
    progress_callback: LoadProgressCallback | None,
    parse_rows: Callable[..., ParsedRows],
    plan_rows: PlanRows,
    error_row_count: ErrorRowCount,
) -> LoadRunResult:
    if not dry_run and not yes:
        raise ConfigError("Non-dry-run load requires --yes.")
    mode = workflow_mode(dry_run=dry_run, retry_statuses=retry_statuses)
    parsed = parse_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
    )
    started_at = _utc_iso()
    run_id = _run_id(job.name)
    run_dir = runtime_path(LOAD_RUNS_DIR / run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    responses: list[LoadResponse] = []
    if dry_run:
        if materialized is None:
            plan = plan_rows(parsed, None)
            issues = (*parsed["issues"], *plan.issues)
            error_rows = error_row_count(issues)
            materialized = _materialized_without_progress(
                job,
                workbook_path,
                parsed,
                valid_rows=max(len(parsed["rows"]) - error_rows, 0),
                error_rows=error_rows,
                issues=issues,
                requests=tuple(plan.requests),
            )
        requests = list(materialized.requests)
        issues = list(materialized.issues)
        _write_requests(run_dir / "requests.jsonl", tuple(requests))
    else:
        planned = plan_rows(parsed, None)
        if planned.requests and auth_ctx is None:
            raise ConfigError("Load run requires an auth context.")
        total = len(planned.requests)
        index = 0
        requests: list[LoadRequest] = []

        def execute(request: LoadRequest) -> LoadResponse:
            nonlocal index
            index += 1
            return _execute_chained_load_request(
                auth_ctx,
                request,
                index=index,
                total=total,
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )

        executed = plan_rows(parsed, execute)
        issues = [*parsed["issues"], *executed.issues]
        _write_requests(run_dir / "requests.jsonl", tuple(requests))
        _write_responses(run_dir / "responses.jsonl", tuple(responses))

    _emit_progress(
        progress_callback,
        {
            "event": "load_artifacts",
            "run_dir": str(run_dir),
            "requests": len(requests),
        },
    )
    finished_at = _utc_iso()
    error_rows = error_row_count(issues)
    materialized = _materialized_without_progress(
        job,
        workbook_path,
        parsed,
        valid_rows=max(len(parsed["rows"]) - error_rows, 0),
        error_rows=error_rows,
        issues=tuple(issues),
        requests=tuple(requests),
    )
    review_path = None
    if responses or _has_row_issues(materialized.issues):
        review_path = _write_review_workbook(
            materialized,
            responses=tuple(responses),
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
        workbook_path=Path(workbook_path).expanduser(),
        sheet=str(parsed["sheet"]),
        rows_scanned=int(parsed["rows_scanned"]),
        valid_rows=materialized.valid_rows,
        error_rows=materialized.error_rows,
        request_count=len(requests),
        success_count=sum(1 for response in responses if response.ok),
        failure_count=sum(1 for response in responses if not response.ok),
        issues=tuple(issues),
        requests=tuple(requests),
        responses=tuple(responses),
        run_dir=run_dir,
        review_path=review_path,
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_summary(run_dir / "summary.json", result, config)
    return result


def materialize_from_parsed(
    job: LoadJob,
    workbook_path: Path,
    parsed: ParsedRows,
    *,
    requests: tuple[LoadRequest, ...],
    progress_callback: LoadProgressCallback | None,
    valid_rows: int | None = None,
    error_rows: int | None = None,
    issues: tuple[LoadIssue, ...] | None = None,
) -> LoadMaterialized:
    valid = len(parsed["rows"]) if valid_rows is None else valid_rows
    errors = int(parsed["error_rows"]) if error_rows is None else error_rows
    _emit_progress(
        progress_callback,
        {
            "event": "load_validate",
            "scanned": parsed["rows_scanned"],
            "valid": valid,
            "errors": errors,
        },
    )
    return _materialized_without_progress(
        job,
        workbook_path,
        parsed,
        valid_rows=valid,
        error_rows=errors,
        issues=tuple(parsed["issues"]) if issues is None else issues,
        requests=requests,
    )


def workflow_mode(*, dry_run: bool, retry_statuses: set[str] | None) -> str:
    if retry_statuses and dry_run:
        return "retry-dry-run"
    if retry_statuses:
        return "retry"
    return "dry-run" if dry_run else "run"


def _materialized_without_progress(
    job: LoadJob,
    workbook_path: Path,
    parsed: ParsedRows,
    *,
    requests: tuple[LoadRequest, ...],
    valid_rows: int | None = None,
    error_rows: int | None = None,
    issues: tuple[LoadIssue, ...] | None = None,
) -> LoadMaterialized:
    return LoadMaterialized(
        job_name=job.name,
        title=job.title,
        workbook_path=Path(workbook_path).expanduser(),
        sheet=str(parsed["sheet"]),
        header_row=job.input.header_row,
        rows_scanned=int(parsed["rows_scanned"]),
        valid_rows=len(parsed["rows"]) if valid_rows is None else valid_rows,
        error_rows=int(parsed["error_rows"]) if error_rows is None else error_rows,
        issues=tuple(parsed["issues"]) if issues is None else issues,
        requests=requests,
    )
