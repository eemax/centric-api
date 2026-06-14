from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..auth import AuthContext
from ..config import ConfigError
from ..load_config import LoadConfig, LoadJob
from ..store import connect_readonly, endpoint_has_cache_evidence, table_exists
from .artifacts import _execute_chained_load_request, _response_field
from .models import (
    LoadIssue,
    LoadMaterialized,
    LoadProgressCallback,
    LoadRequest,
    LoadResponse,
    LoadRunResult,
    StyleBomRow,
)
from .utils import _is_blank, _json_dict
from .workflow import materialize_chained_workflow, parse_workflow_rows, run_chained_workflow


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
    return materialize_chained_workflow(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        parse_rows=_style_bom_rows,
        planned_requests=_style_bom_planned_requests,
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
    return run_chained_workflow(
        db_path,
        config,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        dry_run=dry_run,
        yes=yes,
        retry_statuses=retry_statuses,
        materialized=materialized,
        auth_ctx=auth_ctx,
        progress_callback=progress_callback,
        parse_rows=_style_bom_rows,
        planned_requests=_style_bom_planned_requests,
        execute_rows=_execute_style_bom_workflow,
    )


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
    section_names = _style_bom_section_names(db_path)

    def validate_values(values: dict[str, Any], row_number: int) -> list[LoadIssue]:
        section_name = values.get("section")
        if _is_blank(section_name) or str(section_name) in section_names:
            return []
        return [
            LoadIssue(
                row=row_number,
                code="bom_section_not_found",
                column="section",
                message=(
                    f"Section {section_name!r} was not found exactly in bom_sections.node_name."
                ),
            )
        ]

    return parse_workflow_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        require_columns=_require_style_bom_columns,
        row_factory=lambda row_number, values: StyleBomRow(row=row_number, values=values),
        validate_values=validate_values,
    )


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
