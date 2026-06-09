from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

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
    MAX_SAMPLES,
    REVIEW_WORKBOOK_NAME,
    LoadIssue,
    LoadMaterialized,
    LoadProgressCallback,
    LoadRequest,
    LoadResponse,
    LoadRunResult,
    MaterialSupplierQuoteRow,
    StyleSupplierQuoteReferences,
    StyleSupplierQuoteRow,
)
from .references import _build_reference_indexes, _build_value_set_indexes, _row_values
from .utils import _extract_path, _is_blank, _json_dict, _lookup_key, _run_id, _utc_iso


def materialize_style_supplier_quote_workflow(
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
    return _materialize_supplier_quote_workflow(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        root="style",
    )


def materialize_material_supplier_quote_workflow(
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
    return _materialize_supplier_quote_workflow(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        root="material",
    )


def _materialize_supplier_quote_workflow(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    mode: str,
    retry_statuses: set[str] | None,
    progress_callback: LoadProgressCallback | None,
    root: str,
) -> LoadMaterialized:
    parsed = _supplier_quote_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        root=root,
    )
    requests = _supplier_quote_planned_requests(parsed["rows"], root=root)
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


def run_style_supplier_quote_workflow(
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
    return _run_supplier_quote_workflow(
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
        root="style",
    )


def run_material_supplier_quote_workflow(
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
    return _run_supplier_quote_workflow(
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
        root="material",
    )


def _run_supplier_quote_workflow(
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
    root: str,
) -> LoadRunResult:
    if not dry_run and not yes:
        raise ConfigError("Non-dry-run load requires --yes.")
    mode = (
        "retry-dry-run"
        if retry_statuses and dry_run
        else ("retry" if retry_statuses else ("dry-run" if dry_run else "run"))
    )
    parsed = _supplier_quote_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        root=root,
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
        requests=tuple(_supplier_quote_planned_requests(parsed["rows"], root=root)),
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
            workflow_issues = _execute_supplier_quote_workflow(
                auth_ctx,
                parsed["rows"],
                root=root,
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


def _style_supplier_quote_rows(
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
    return _supplier_quote_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        root="style",
    )


def _material_supplier_quote_rows(
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
    return _supplier_quote_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        root="material",
    )


def _supplier_quote_rows(
    db_path: Path,
    job: LoadJob,
    workbook_path: Path,
    *,
    sheet: str | None,
    limit: int | None,
    mode: str,
    retry_statuses: set[str] | None,
    progress_callback: LoadProgressCallback | None,
    root: str,
) -> dict[str, Any]:
    _require_supplier_quote_columns(job, root=root)
    workbook_path = workbook_path.expanduser()
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    supplier_refs = _style_supplier_quote_references(db_path)
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
        rows: list[StyleSupplierQuoteRow | MaterialSupplierQuoteRow] = []
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
                if not row_issues:
                    row_issues.extend(
                        _resolve_style_supplier_quote_memberships(
                            values,
                            row_number=row_number,
                            refs=supplier_refs,
                        )
                    )
                if row_issues:
                    error_rows += 1
                    issues.extend(row_issues)
                    continue
                if root == "style":
                    rows.append(StyleSupplierQuoteRow(row=row_number, values=values))
                else:
                    rows.append(MaterialSupplierQuoteRow(row=row_number, values=values))
        return {
            "sheet": worksheet.title,
            "rows_scanned": rows_scanned,
            "error_rows": error_rows,
            "issues": issues,
            "rows": tuple(rows),
        }
    finally:
        workbook.close()


def _require_style_supplier_quote_columns(job: LoadJob) -> None:
    _require_supplier_quote_columns(job, root="style")


def _require_supplier_quote_columns(job: LoadJob, *, root: str) -> None:
    required = {
        root,
        "supplier",
        "node_name",
        "description",
        "quote_factory",
        "set_production_quote",
    }
    if root == "style":
        required.add("season")
    present = {column.key for column in job.columns}
    missing = sorted(required - present)
    if missing:
        raise ConfigError(
            f"load job[{job.name}] workflow {root}_supplier_quote is missing columns: "
            f"{', '.join(missing)}."
        )


def _style_supplier_quote_references(db_path: Path) -> StyleSupplierQuoteReferences:
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "endpoint_records"):
            raise ConfigError("Supplier quote load requires endpoint_records. Run fetch first.")
        if not endpoint_has_cache_evidence(conn, "suppliers"):
            raise ConfigError(
                "Supplier quote load requires cached endpoint records for: suppliers. "
                "Run centric-api fetch --endpoint suppliers first."
            )
        suppliers = _endpoint_payloads(conn, "suppliers")
        factories = (
            _endpoint_payloads(conn, "factories")
            if endpoint_has_cache_evidence(conn, "factories")
            else ()
        )
    return StyleSupplierQuoteReferences(suppliers=suppliers, factories=factories)


def _endpoint_payloads(conn: sqlite3.Connection, endpoint: str) -> tuple[dict[str, Any], ...]:
    rows = conn.execute(
        """
        SELECT payload_json
        FROM endpoint_records
        WHERE endpoint = ?
        ORDER BY record_id
        """,
        [endpoint],
    ).fetchall()
    return tuple(_json_dict(row["payload_json"]) for row in rows)


def _resolve_style_supplier_quote_memberships(
    values: dict[str, Any],
    *,
    row_number: int,
    refs: StyleSupplierQuoteReferences,
) -> list[LoadIssue]:
    issues: list[LoadIssue] = []
    supplier = _resolve_style_supplier_quote_record(
        refs.suppliers,
        values.get("supplier"),
        row_number=row_number,
        column="supplier",
        label="Supplier",
    )
    if isinstance(supplier, LoadIssue):
        return [supplier]
    if supplier.get("is_supplier") is not True:
        issues.append(
            LoadIssue(
                row=row_number,
                code="supplier_not_supplier",
                column="supplier",
                message=f"Supplier {values.get('supplier')!r} is not marked as a supplier.",
                sample=supplier.get("id"),
            )
        )
        return issues
    values["supplier"] = str(supplier["id"]).strip()

    if not _is_blank(values.get("agent")):
        agent = _resolve_style_supplier_quote_record(
            refs.suppliers,
            values.get("agent"),
            row_number=row_number,
            column="agent",
            label="Agent",
        )
        if isinstance(agent, LoadIssue):
            issues.append(agent)
        elif agent.get("is_agent") is not True:
            issues.append(
                LoadIssue(
                    row=row_number,
                    code="agent_not_agent",
                    column="agent",
                    message=f"Agent {values.get('agent')!r} is not marked as an agent.",
                    sample=agent.get("id"),
                )
            )
        elif not _record_has_ref(supplier.get("all_agents"), str(agent.get("id"))):
            issues.append(
                LoadIssue(
                    row=row_number,
                    code="agent_not_linked_to_supplier",
                    column="agent",
                    message=f"Agent {values.get('agent')!r} is not linked to the supplier.",
                    sample=agent.get("id"),
                )
            )
        else:
            values["agent"] = str(agent["id"]).strip()

    if not _is_blank(values.get("quote_factory")):
        if not refs.factories:
            issues.append(
                LoadIssue(
                    row=row_number,
                    code="factory_cache_missing",
                    column="quote_factory",
                    message=(
                        "Quote Factory requires cached endpoint records for: factories. "
                        "Run centric-api fetch --endpoint factories first."
                    ),
                )
            )
            return issues
        factory = _resolve_style_supplier_quote_record(
            refs.factories,
            values.get("quote_factory"),
            row_number=row_number,
            column="quote_factory",
            label="Quote Factory",
        )
        if isinstance(factory, LoadIssue):
            issues.append(factory)
        elif not _record_has_ref(factory.get("suppliers"), str(supplier.get("id"))):
            issues.append(
                LoadIssue(
                    row=row_number,
                    code="factory_not_linked_to_supplier",
                    column="quote_factory",
                    message=(
                        f"Quote Factory {values.get('quote_factory')!r} is not linked "
                        "to the supplier."
                    ),
                    sample=factory.get("id"),
                )
            )
        else:
            values["quote_factory"] = str(factory["id"]).strip()
    return issues


def _resolve_style_supplier_quote_record(
    records: tuple[dict[str, Any], ...],
    value: Any,
    *,
    row_number: int,
    column: str,
    label: str,
) -> dict[str, Any] | LoadIssue:
    text = str(value or "").strip()
    lookup = _lookup_key(text)
    matches: dict[str, dict[str, Any]] = {}
    for record in records:
        for path in ("node_name", "supplier_number"):
            candidate = _extract_path(record, path)
            if _is_blank(candidate) or _lookup_key(str(candidate)) != lookup:
                continue
            record_id = str(record.get("id") or "").strip()
            if record_id:
                matches[record_id] = record
    if not matches:
        return LoadIssue(
            row=row_number,
            code=f"{column}_not_found",
            column=column,
            message=f"{label} {text!r} was not found by node_name or supplier_number.",
        )
    if len(matches) > 1:
        return LoadIssue(
            row=row_number,
            code=f"{column}_ambiguous",
            column=column,
            message=f"{label} {text!r} matched {len(matches)} records.",
            sample=sorted(matches)[:MAX_SAMPLES],
        )
    return next(iter(matches.values()))


def _record_has_ref(value: Any, expected: str) -> bool:
    expected_key = _ref_key(expected)
    return any(_ref_key(ref) == expected_key for ref in _iter_refs(value))


def _iter_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_refs(item)
    elif isinstance(value, list | tuple | set):
        for item in value:
            yield from _iter_refs(item)
    elif not _is_blank(value):
        yield str(value)


def _ref_key(value: str) -> str:
    return unquote(value).strip()


def _style_supplier_quote_planned_requests(
    rows: tuple[StyleSupplierQuoteRow, ...],
) -> tuple[LoadRequest, ...]:
    return _supplier_quote_planned_requests(rows, root="style")


def _material_supplier_quote_planned_requests(
    rows: tuple[MaterialSupplierQuoteRow, ...],
) -> tuple[LoadRequest, ...]:
    return _supplier_quote_planned_requests(rows, root="material")


def _supplier_quote_planned_requests(
    rows: tuple[StyleSupplierQuoteRow, ...] | tuple[MaterialSupplierQuoteRow, ...],
    *,
    root: str,
) -> tuple[LoadRequest, ...]:
    requests: list[LoadRequest] = []
    for row in rows:
        requests.append(_supplier_quote_product_source_request(row, root=root))
        requests.append(
            _supplier_quote_item_request(row, product_source_id="DRY-RUN-PRODUCT-SOURCE")
        )
        if not _is_blank(row.values.get("quote_factory")):
            requests.append(
                _supplier_quote_revision_request(row, revision_id="DRY-RUN-REVISION")
            )
        if row.values.get("set_production_quote") is True:
            requests.append(
                _supplier_quote_production_request(
                    row,
                    root=root,
                    supplier_item_id="DRY-RUN-SUPPLIER-ITEM",
                )
            )
    return tuple(requests)


def _execute_style_supplier_quote_workflow(
    auth_ctx: AuthContext,
    rows: tuple[StyleSupplierQuoteRow, ...],
    *,
    requests: list[LoadRequest],
    responses: list[LoadResponse],
    progress_callback: LoadProgressCallback | None,
) -> list[LoadIssue]:
    return _execute_supplier_quote_workflow(
        auth_ctx,
        rows,
        root="style",
        requests=requests,
        responses=responses,
        progress_callback=progress_callback,
    )


def _execute_supplier_quote_workflow(
    auth_ctx: AuthContext,
    rows: tuple[StyleSupplierQuoteRow, ...] | tuple[MaterialSupplierQuoteRow, ...],
    *,
    root: str,
    requests: list[LoadRequest],
    responses: list[LoadResponse],
    progress_callback: LoadProgressCallback | None,
) -> list[LoadIssue]:
    issues: list[LoadIssue] = []
    total = sum(
        2
        + (0 if _is_blank(row.values.get("quote_factory")) else 1)
        + (1 if row.values.get("set_production_quote") is True else 0)
        for row in rows
    )
    index = 0
    for row in rows:
        product_source_request = _supplier_quote_product_source_request(row, root=root)
        index += 1
        product_source_response = _execute_chained_load_request(
            auth_ctx,
            product_source_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        product_source_id = _response_field(product_source_response, "id")
        if not product_source_response.ok or _is_blank(product_source_id):
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="product_source_create_failed",
                    message="Product source request failed or returned no id.",
                )
            )
            continue

        item_request = _supplier_quote_item_request(
            row,
            product_source_id=str(product_source_id),
        )
        index += 1
        item_response = _execute_chained_load_request(
            auth_ctx,
            item_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        supplier_item_id = _response_field(item_response, "id")
        revision_id = _response_field(item_response, "latest_revision") or _response_field(
            item_response,
            "current_revision",
        )
        if not item_response.ok or _is_blank(supplier_item_id) or _is_blank(revision_id):
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="supplier_item_create_failed",
                    message=(
                        "Supplier item request failed or returned no supplier item/revision id."
                    ),
                )
            )
            continue

        if not _is_blank(row.values.get("quote_factory")):
            revision_request = _supplier_quote_revision_request(
                row,
                revision_id=str(revision_id),
            )
            index += 1
            revision_response = _execute_chained_load_request(
                auth_ctx,
                revision_request,
                index=index,
                total=total,
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )
            if not revision_response.ok:
                issues.append(
                    LoadIssue(
                        row=row.row,
                        code="supplier_item_revision_update_failed",
                        message="Supplier item revision update failed.",
                    )
                )

        if row.values.get("set_production_quote") is True:
            production_request = _supplier_quote_production_request(
                row,
                root=root,
                supplier_item_id=str(supplier_item_id),
            )
            index += 1
            production_response = _execute_chained_load_request(
                auth_ctx,
                production_request,
                index=index,
                total=total,
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )
            if not production_response.ok:
                label = "Material default quote" if root == "material" else "Style production quote"
                issues.append(
                    LoadIssue(
                        row=row.row,
                        code="production_quote_update_failed",
                        message=f"{label} update failed.",
                    )
                )
    return issues


def _style_supplier_quote_product_source_request(row: StyleSupplierQuoteRow) -> LoadRequest:
    return _supplier_quote_product_source_request(row, root="style")


def _supplier_quote_product_source_request(
    row: StyleSupplierQuoteRow | MaterialSupplierQuoteRow,
    *,
    root: str,
) -> LoadRequest:
    values = row.values
    body = {"supplier": values["supplier"]}
    if not _is_blank(values.get("agent")):
        body["agent"] = values["agent"]
    return LoadRequest(
        row=row.row,
        method="POST",
        path=f"/v2/{root}s/{values[root]}/product_sources",
        body=body,
    )


def _style_supplier_quote_item_request(
    row: StyleSupplierQuoteRow,
    *,
    product_source_id: str,
) -> LoadRequest:
    return _supplier_quote_item_request(row, product_source_id=product_source_id)


def _supplier_quote_item_request(
    row: StyleSupplierQuoteRow | MaterialSupplierQuoteRow,
    *,
    product_source_id: str,
) -> LoadRequest:
    values = row.values
    body = {"node_name": values["node_name"]}
    if not _is_blank(values.get("description")):
        body["description"] = values["description"]
    return LoadRequest(
        row=row.row,
        method="POST",
        path=f"/v2/product_sources/{product_source_id}/supplier_items",
        body=body,
    )


def _style_supplier_quote_revision_request(
    row: StyleSupplierQuoteRow,
    *,
    revision_id: str,
) -> LoadRequest:
    return _supplier_quote_revision_request(row, revision_id=revision_id)


def _supplier_quote_revision_request(
    row: StyleSupplierQuoteRow | MaterialSupplierQuoteRow,
    *,
    revision_id: str,
) -> LoadRequest:
    return LoadRequest(
        row=row.row,
        method="PUT",
        path=f"/v2/supplier_item_revisions/{revision_id}",
        body={"quote_factory": row.values["quote_factory"]},
    )


def _style_supplier_quote_production_request(
    row: StyleSupplierQuoteRow,
    *,
    supplier_item_id: str,
) -> LoadRequest:
    return _supplier_quote_production_request(
        row,
        root="style",
        supplier_item_id=supplier_item_id,
    )


def _supplier_quote_production_request(
    row: StyleSupplierQuoteRow | MaterialSupplierQuoteRow,
    *,
    root: str,
    supplier_item_id: str,
) -> LoadRequest:
    body_key = "default_quote" if root == "material" else "production_quote"
    return LoadRequest(
        row=row.row,
        method="PUT",
        path=f"/v2/{root}s/{row.values[root]}",
        body={body_key: supplier_item_id},
    )
