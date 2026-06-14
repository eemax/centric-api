from __future__ import annotations

from pathlib import Path
from typing import Any

from ..auth import AuthContext
from ..config import ConfigError
from ..load_config import LoadConfig, LoadJob
from .artifacts import _execute_chained_load_request, _response_field
from .models import (
    LoadIssue,
    LoadMaterialized,
    LoadProgressCallback,
    LoadRequest,
    LoadResponse,
    LoadRunResult,
    MaterialCreateCompositionQuoteRow,
    MaterialCreateCompositionRow,
    MaterialSupplierQuoteRow,
)
from .style_supplier_quote import (
    _resolve_style_supplier_quote_memberships,
    _style_supplier_quote_references,
    _supplier_quote_item_request,
    _supplier_quote_product_source_request,
    _supplier_quote_production_request,
    _supplier_quote_revision_request,
)
from .utils import _is_blank
from .workflow import materialize_chained_workflow, parse_workflow_rows, run_chained_workflow


def materialize_material_create_with_composition_workflow(
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
        parse_rows=_material_create_composition_rows,
        planned_requests=_material_create_composition_planned_requests,
    )


def materialize_material_create_with_composition_and_quote_workflow(
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
        parse_rows=_material_create_composition_quote_rows,
        planned_requests=_material_create_composition_quote_planned_requests,
    )


def run_material_create_with_composition_workflow(
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
        parse_rows=_material_create_composition_rows,
        planned_requests=_material_create_composition_planned_requests,
        execute_rows=_execute_material_create_composition_workflow,
    )


def run_material_create_with_composition_and_quote_workflow(
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
        parse_rows=_material_create_composition_quote_rows,
        planned_requests=_material_create_composition_quote_planned_requests,
        execute_rows=_execute_material_create_composition_quote_workflow,
    )


def _material_create_composition_rows(
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
    return parse_workflow_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        require_columns=_require_material_create_composition_columns,
        row_factory=lambda row_number, values: MaterialCreateCompositionRow(
            row=row_number,
            values=values,
        ),
    )


def _material_create_composition_quote_rows(
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
    supplier_refs = _style_supplier_quote_references(db_path)
    return parse_workflow_rows(
        db_path,
        job,
        workbook_path,
        sheet=sheet,
        limit=limit,
        mode=mode,
        retry_statuses=retry_statuses,
        progress_callback=progress_callback,
        require_columns=_require_material_create_composition_quote_columns,
        row_factory=lambda row_number, values: MaterialCreateCompositionQuoteRow(
            row=row_number,
            values=values,
        ),
        validate_values=lambda values, row_number: _resolve_style_supplier_quote_memberships(
            values,
            row_number=row_number,
            refs=supplier_refs,
        ),
    )


def _require_material_create_composition_columns(job: LoadJob) -> None:
    required = {
        "code",
        "product_type",
        "description",
        "compositions",
    }
    present = {column.key for column in job.columns}
    missing = sorted(required - present)
    if missing:
        raise ConfigError(
            f"load job[{job.name}] workflow material_create_with_composition "
            f"is missing columns: {', '.join(missing)}."
        )


def _require_material_create_composition_quote_columns(job: LoadJob) -> None:
    required = {
        "code",
        "product_type",
        "material_description",
        "compositions",
        "supplier",
        "agent",
        "node_name",
        "quote_description",
        "quote_factory",
        "set_production_quote",
    }
    present = {column.key for column in job.columns}
    missing = sorted(required - present)
    if missing:
        raise ConfigError(
            "load job"
            f"[{job.name}] workflow material_create_with_composition_and_quote "
            f"is missing columns: {', '.join(missing)}."
        )


def _material_create_composition_planned_requests(
    rows: tuple[MaterialCreateCompositionRow, ...],
) -> tuple[LoadRequest, ...]:
    requests: list[LoadRequest] = []
    for row in rows:
        requests.append(_material_create_request(row))
        requests.append(
            _material_composition_request(row, material_id="DRY-RUN-MATERIAL")
        )
    return tuple(requests)


def _material_create_composition_quote_planned_requests(
    rows: tuple[MaterialCreateCompositionQuoteRow, ...],
) -> tuple[LoadRequest, ...]:
    requests: list[LoadRequest] = []
    for row in rows:
        requests.append(_material_create_request(row))
        requests.append(
            _material_composition_request(row, material_id="DRY-RUN-MATERIAL")
        )
        quote_row = _material_supplier_quote_row(row, material_id="DRY-RUN-MATERIAL")
        requests.append(_supplier_quote_product_source_request(quote_row, root="material"))
        requests.append(
            _supplier_quote_item_request(quote_row, product_source_id="DRY-RUN-PRODUCT-SOURCE")
        )
        if not _is_blank(row.values.get("quote_factory")):
            requests.append(
                _supplier_quote_revision_request(quote_row, revision_id="DRY-RUN-REVISION")
            )
        if row.values.get("set_production_quote") is True:
            requests.append(
                _supplier_quote_production_request(
                    quote_row,
                    root="material",
                    supplier_item_id="DRY-RUN-SUPPLIER-ITEM",
                )
            )
    return tuple(requests)


def _execute_material_create_composition_workflow(
    auth_ctx: AuthContext,
    rows: tuple[MaterialCreateCompositionRow, ...],
    *,
    requests: list[LoadRequest],
    responses: list[LoadResponse],
    progress_callback: LoadProgressCallback | None,
) -> list[LoadIssue]:
    issues: list[LoadIssue] = []
    total = len(rows) * 2
    index = 0
    for row in rows:
        create_request = _material_create_request(row)
        index += 1
        create_response = _execute_chained_load_request(
            auth_ctx,
            create_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        material_id = _response_field(create_response, "id")
        if not create_response.ok:
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="material_create_failed",
                    message="Material create request failed.",
                )
            )
            continue
        if _is_blank(material_id):
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="material_id_missing",
                    message="Material create response did not include id.",
                )
            )
            continue

        composition_request = _material_composition_request(
            row,
            material_id=str(material_id),
        )
        index += 1
        composition_response = _execute_chained_load_request(
            auth_ctx,
            composition_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        if not composition_response.ok:
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="material_composition_create_failed",
                    message="Material composition request failed.",
                )
            )
    return issues


def _execute_material_create_composition_quote_workflow(
    auth_ctx: AuthContext,
    rows: tuple[MaterialCreateCompositionQuoteRow, ...],
    *,
    requests: list[LoadRequest],
    responses: list[LoadResponse],
    progress_callback: LoadProgressCallback | None,
) -> list[LoadIssue]:
    issues: list[LoadIssue] = []
    total = sum(
        4
        + (0 if _is_blank(row.values.get("quote_factory")) else 1)
        + (1 if row.values.get("set_production_quote") is True else 0)
        for row in rows
    )
    index = 0
    for row in rows:
        create_request = _material_create_request(row)
        index += 1
        create_response = _execute_chained_load_request(
            auth_ctx,
            create_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        material_id = _response_field(create_response, "id")
        if not create_response.ok:
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="material_create_failed",
                    message="Material create request failed.",
                )
            )
            continue
        if _is_blank(material_id):
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="material_id_missing",
                    message="Material create response did not include id.",
                )
            )
            continue

        material_id = str(material_id)
        composition_request = _material_composition_request(row, material_id=material_id)
        index += 1
        composition_response = _execute_chained_load_request(
            auth_ctx,
            composition_request,
            index=index,
            total=total,
            requests=requests,
            responses=responses,
            progress_callback=progress_callback,
        )
        if not composition_response.ok:
            issues.append(
                LoadIssue(
                    row=row.row,
                    code="material_composition_create_failed",
                    message="Material composition request failed.",
                )
            )

        quote_row = _material_supplier_quote_row(row, material_id=material_id)
        product_source_request = _supplier_quote_product_source_request(
            quote_row,
            root="material",
        )
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
            quote_row,
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
                quote_row,
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
                quote_row,
                root="material",
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
                issues.append(
                    LoadIssue(
                        row=row.row,
                        code="production_quote_update_failed",
                        message="Material default quote update failed.",
                    )
                )
    return issues


def _material_create_request(
    row: MaterialCreateCompositionRow | MaterialCreateCompositionQuoteRow,
) -> LoadRequest:
    values = row.values
    body = {
        "code": values["code"],
        "product_type": values["product_type"],
    }
    description = values.get("description", values.get("material_description"))
    if not _is_blank(description):
        body["description"] = description
    return LoadRequest(
        row=row.row,
        method="POST",
        path="/v2/materials",
        body=body,
    )


def _material_composition_request(
    row: MaterialCreateCompositionRow | MaterialCreateCompositionQuoteRow,
    *,
    material_id: str,
) -> LoadRequest:
    return LoadRequest(
        row=row.row,
        method="POST",
        path=f"/v2/materials/{material_id}/technical_compositions",
        body=row.values["compositions"],
    )


def _material_supplier_quote_row(
    row: MaterialCreateCompositionQuoteRow,
    *,
    material_id: str,
) -> MaterialSupplierQuoteRow:
    values = dict(row.values)
    values["material"] = material_id
    values["description"] = values.get("quote_description")
    return MaterialSupplierQuoteRow(row=row.row, values=values)
