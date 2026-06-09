from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..auth import AuthContext
from ..config import ConfigError, runtime_path
from ..load_config import LoadConfig, LoadJob
from ..store import connect_readonly, endpoint_has_cache_evidence, table_exists
from .artifacts import (
    _emit_progress,
    _execute_chained_load_request,
    _has_row_issues,
    _response_field,
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
    StyleBomRow,
)
from .references import _build_reference_indexes, _build_value_set_indexes, _row_values
from .utils import _is_blank, _json_dict, _run_id, _utc_iso


def materialize_style_bom_workflow(
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
    parsed = _style_bom_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
    )
    requests = _style_bom_planned_requests(parsed["rows"])
    _emit_progress(
        progress_callback,
        {
            "event": "load_validate",
            "scanned": parsed["rows_scanned"],
            "valid": len(parsed["rows"]),
            "errors": parsed["error_rows"],
        },
    )
    return LoadMaterialized(
        job_name=job.name,
        title=job.title,
        workbook_path=Path(workbook_path).expanduser(),
        sheet=str(parsed["sheet"]),
        header_row=job.input.header_row,
        rows_scanned=int(parsed["rows_scanned"]),
        valid_rows=len(parsed["rows"]),
        error_rows=int(parsed["error_rows"]),
        issues=tuple(parsed["issues"]),
        requests=tuple(requests),
    )


def run_style_bom_workflow(
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
    if not dry_run and not yes:
        raise ConfigError("Non-dry-run load requires --yes.")
    mode = (
        "retry-dry-run"
        if retry_statuses and dry_run
        else ("retry" if retry_statuses else ("dry-run" if dry_run else "run"))
    )
    parsed = _style_bom_rows(
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

    materialized = materialized or LoadMaterialized(
        job_name=job.name,
        title=job.title,
        workbook_path=Path(workbook_path).expanduser(),
        sheet=str(parsed["sheet"]),
        header_row=job.input.header_row,
        rows_scanned=int(parsed["rows_scanned"]),
        valid_rows=len(parsed["rows"]),
        error_rows=int(parsed["error_rows"]),
        issues=tuple(parsed["issues"]),
        requests=tuple(_style_bom_planned_requests(parsed["rows"])),
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
            workflow_issues = _execute_style_bom_workflow(
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
    materialized = LoadMaterialized(
        job_name=job.name,
        title=job.title,
        workbook_path=Path(workbook_path).expanduser(),
        sheet=str(parsed["sheet"]),
        header_row=job.input.header_row,
        rows_scanned=int(parsed["rows_scanned"]),
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


def _style_bom_rows(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    mode: str,
    retry_statuses: set[str] | None,
    progress_callback: LoadProgressCallback | None,
) -> dict[str, Any]:
    _require_style_bom_columns(job)
    workbook_path = workbook_path.expanduser()
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    section_names = _style_bom_section_names(db_path)
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
        rows: list[StyleBomRow] = []
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
                section_name = values.get("section")
                if not _is_blank(section_name) and str(section_name) not in section_names:
                    row_issues.append(
                        LoadIssue(
                            row=row_number,
                            code="bom_section_not_found",
                            column="section",
                            message=(
                                f"Section {section_name!r} was not found exactly in "
                                "bom_sections.node_name."
                            ),
                        )
                    )
                if row_issues:
                    error_rows += 1
                    issues.extend(row_issues)
                    continue
                rows.append(StyleBomRow(row=row_number, values=values))
        return {
            "sheet": worksheet.title,
            "rows_scanned": rows_scanned,
            "error_rows": error_rows,
            "issues": issues,
            "rows": tuple(rows),
        }
    finally:
        workbook.close()


def _require_style_bom_columns(job: LoadJob) -> None:
    required = {
        "season",
        "style",
        "node_name",
        "description",
        "subtype",
        "section",
        "actual",
    }
    present = {column.key for column in job.columns}
    missing = sorted(required - present)
    if missing:
        raise ConfigError(
            f"load job[{job.name}] workflow style_bom is missing columns: {', '.join(missing)}."
        )


def _style_bom_section_names(db_path: Path) -> set[str]:
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_records"):
            raise ConfigError("BOM load requires endpoint_records. Run fetch first.")
        if not endpoint_has_cache_evidence(conn, "bom_sections"):
            raise ConfigError(
                "BOM load requires cached endpoint records for: bom_sections. "
                "Run centric-api fetch --endpoint bom_sections first."
            )
        rows = conn.execute(
            """
            SELECT payload_json
            FROM endpoint_records
            WHERE endpoint = 'bom_sections'
            ORDER BY record_id
            """
        ).fetchall()
    names: set[str] = set()
    for row in rows:
        payload = _json_dict(row["payload_json"])
        if payload.get("active") is not True or payload.get("ad_hoc") is not False:
            continue
        value = payload.get("node_name")
        if not _is_blank(value):
            names.add(str(value))
    return names


def _style_bom_groups(rows: tuple[StyleBomRow, ...]) -> list[list[StyleBomRow]]:
    groups: dict[tuple[str, str, str, str], list[StyleBomRow]] = {}
    for row in rows:
        values = row.values
        key = (
            str(values["style"]),
            str(values["node_name"]),
            str(values.get("description") or ""),
            str(values["subtype"]),
        )
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def _style_bom_planned_requests(rows: tuple[StyleBomRow, ...]) -> tuple[LoadRequest, ...]:
    requests: list[LoadRequest] = []
    for group in _style_bom_groups(rows):
        first = group[0]
        requests.append(_style_bom_header_request(first))
        for section_name in _style_bom_unique_sections(group):
            requests.append(
                LoadRequest(
                    row=_style_bom_first_section_row(group, section_name),
                    method="POST",
                    path=(
                        "/v2/apparel_bom_revisions/DRY-RUN-REVISION/"
                        "owned_sections/bom_section_definition"
                    ),
                    body={"node_name": section_name},
                )
            )
        for row in group:
            requests.append(
                _style_bom_line_request(
                    row,
                    revision_id="DRY-RUN-REVISION",
                    section_id=f"DRY-RUN-SECTION-{_slug(str(row.values['section']))}",
                )
            )
    return tuple(requests)


def _execute_style_bom_workflow(
    auth_ctx: AuthContext,
    rows: tuple[StyleBomRow, ...],
    *,
    requests: list[LoadRequest],
    responses: list[LoadResponse],
    progress_callback: LoadProgressCallback | None,
) -> list[LoadIssue]:
    issues: list[LoadIssue] = []
    groups = _style_bom_groups(rows)
    total = sum(1 + len(_style_bom_unique_sections(group)) + len(group) for group in groups)
    index = 0
    for group in groups:
        first = group[0]
        header_request = _style_bom_header_request(first)
        index += 1
        header_response = _execute_chained_load_request(
            auth_ctx,
            header_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        if not header_response.ok:
            issues.extend(
                _style_bom_group_issues(group, "bom_header_failed", "BOM header request failed.")
            )
            continue
        revision_id = _response_field(header_response, "latest_revision") or _response_field(
            header_response,
            "current_revision",
        )
        if _is_blank(revision_id):
            issues.extend(
                _style_bom_group_issues(
                    group,
                    "bom_revision_missing",
                    "BOM header response did not include latest_revision or current_revision.",
                )
            )
            continue

        section_ids: dict[str, str] = {}
        for section_name in _style_bom_unique_sections(group):
            section_request = LoadRequest(
                row=_style_bom_first_section_row(group, section_name),
                method="POST",
                path=(
                    f"/v2/apparel_bom_revisions/{revision_id}/owned_sections/bom_section_definition"
                ),
                body={"node_name": section_name},
            )
            index += 1
            section_response = _execute_chained_load_request(
                auth_ctx,
                section_request,
                index=index,
                total=total,
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )
            section_id = _response_field(section_response, "id")
            if not section_response.ok or _is_blank(section_id):
                issues.extend(
                    LoadIssue(
                        row=row.row,
                        code="bom_section_create_failed",
                        column="section",
                        message=f"Could not create BOM section {section_name!r}.",
                    )
                    for row in group
                    if str(row.values["section"]) == section_name
                )
                continue
            section_ids[section_name] = str(section_id)

        for row in group:
            section_id = section_ids.get(str(row.values["section"]))
            if section_id is None:
                continue
            line_request = _style_bom_line_request(
                row,
                revision_id=str(revision_id),
                section_id=section_id,
            )
            index += 1
            line_response = _execute_chained_load_request(
                auth_ctx,
                line_request,
                index=index,
                total=total,
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )
            if not line_response.ok:
                issues.append(
                    LoadIssue(
                        row=row.row,
                        code="bom_line_create_failed",
                        message="BOM line request failed.",
                    )
                )
    return issues


def _style_bom_header_request(row: StyleBomRow) -> LoadRequest:
    values = row.values
    body = {
        "node_name": values["node_name"],
        "subtype": values["subtype"],
    }
    if not _is_blank(values.get("description")):
        body["description"] = values["description"]
    return LoadRequest(
        row=row.row,
        method="POST",
        path=f"/v2/styles/{values['style']}/data_sheets/apparel_boms",
        body=body,
    )


def _style_bom_line_request(
    row: StyleBomRow,
    *,
    revision_id: str,
    section_id: str,
) -> LoadRequest:
    values = row.values
    body = {
        "ds_section": section_id,
        "actual": values["actual"],
    }
    if not _is_blank(values.get("pm_id")):
        body["pm_id"] = values["pm_id"]
    if not _is_blank(values.get("qty_default")):
        body["qty_default"] = values["qty_default"]
    return LoadRequest(
        row=row.row,
        method="POST",
        path=f"/v2/apparel_bom_revisions/{revision_id}/items/part_materials",
        body=body,
    )


def _style_bom_unique_sections(group: list[StyleBomRow]) -> list[str]:
    seen: set[str] = set()
    sections: list[str] = []
    for row in group:
        section = str(row.values["section"])
        if section in seen:
            continue
        seen.add(section)
        sections.append(section)
    return sections


def _style_bom_first_section_row(group: list[StyleBomRow], section_name: str) -> int:
    for row in group:
        if str(row.values["section"]) == section_name:
            return row.row
    return group[0].row


def _style_bom_group_issues(
    group: list[StyleBomRow],
    code: str,
    message: str,
) -> list[LoadIssue]:
    return [LoadIssue(row=row.row, code=code, message=message) for row in group]


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-")
    return slug or "section"
