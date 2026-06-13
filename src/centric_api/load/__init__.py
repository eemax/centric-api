from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import _write_text_atomic, materialized_record, run_record
from .generic import materialize_load, run_load
from .material import (
    materialize_material_create_with_composition_and_quote_workflow,
    materialize_material_create_with_composition_workflow,
    run_material_create_with_composition_and_quote_workflow,
    run_material_create_with_composition_workflow,
)
from .models import (
    LOAD_RUNS_DIR,
    LOAD_VALUE_SETS_DIR,
    MAX_SAMPLES,
    RETRY_STATUSES,
    REVIEW_COLUMN_HEADERS,
    REVIEW_STATUSES,
    REVIEW_WORKBOOK_NAME,
    LoadIssue,
    LoadMaterialized,
    LoadRequest,
    LoadResponse,
    LoadRunResult,
)
from .private_workflows import private_workflow_function
from .style_bom import materialize_style_bom_workflow, run_style_bom_workflow
from .style_supplier_quote import (
    materialize_material_supplier_quote_workflow,
    materialize_style_supplier_quote_workflow,
    run_material_supplier_quote_workflow,
    run_style_supplier_quote_workflow,
)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows)
    _write_text_atomic(path, text)


__all__ = [
    "MAX_SAMPLES",
    "RETRY_STATUSES",
    "REVIEW_COLUMN_HEADERS",
    "REVIEW_STATUSES",
    "REVIEW_WORKBOOK_NAME",
    "LOAD_RUNS_DIR",
    "LOAD_VALUE_SETS_DIR",
    "LoadIssue",
    "LoadMaterialized",
    "LoadRequest",
    "LoadResponse",
    "LoadRunResult",
    "materialize_load",
    "run_load",
    "materialize_material_create_with_composition_and_quote_workflow",
    "run_material_create_with_composition_and_quote_workflow",
    "materialize_material_create_with_composition_workflow",
    "run_material_create_with_composition_workflow",
    "materialize_material_supplier_quote_workflow",
    "run_material_supplier_quote_workflow",
    "private_workflow_function",
    "materialize_style_bom_workflow",
    "run_style_bom_workflow",
    "materialize_style_supplier_quote_workflow",
    "run_style_supplier_quote_workflow",
    "materialized_record",
    "run_record",
]
