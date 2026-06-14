from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..auth import AuthContext
from ..config import ConfigError
from ..load_config import LoadConfig, LoadJob
from ..store import connect_readonly, endpoint_has_cache_evidence, table_exists
from .artifacts import _execute_chained_load_request, _response_field
from .models import (
    MAX_SAMPLES,
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
from .utils import _extract_path, _is_blank, _json_dict, _lookup_key
from .workflow import materialize_chained_workflow, parse_workflow_rows, run_chained_workflow


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
    return materialize_chained_workflow(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        parse_rows=lambda *args, **kwargs: _supplier_quote_rows(
            *args,
            **kwargs,
            root=root,
        ),
        planned_requests=lambda rows: _supplier_quote_planned_requests(rows, root=root),
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
        parse_rows=lambda *args, **kwargs: _supplier_quote_rows(
            *args,
            **kwargs,
            root=root,
        ),
        planned_requests=lambda rows: _supplier_quote_planned_requests(rows, root=root),
        execute_rows=lambda auth_ctx, rows, *, requests, responses, progress_callback: (
            _execute_supplier_quote_workflow(
                auth_ctx,
                rows,
                root=root,
                requests=requests,
                responses=responses,
                progress_callback=progress_callback,
            )
        ),
    )


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
    supplier_refs = _style_supplier_quote_references(db_path)

    def row_factory(
        row_number: int,
        values: dict[str, Any],
    ) -> StyleSupplierQuoteRow | MaterialSupplierQuoteRow:
        if root == "style":
            return StyleSupplierQuoteRow(row=row_number, values=values)
        return MaterialSupplierQuoteRow(row=row_number, values=values)

    return parse_workflow_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        require_columns=lambda load_job: _require_supplier_quote_columns(
            load_job,
            root=root,
        ),
        row_factory=row_factory,
        validate_values=lambda values, row_number: _resolve_style_supplier_quote_memberships(
            values,
            row_number=row_number,
            refs=supplier_refs,
        ),
    )


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
