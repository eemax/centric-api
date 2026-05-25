from __future__ import annotations

import uuid
from pathlib import Path

from ..store import connect
from ..units import load_unit_registry
from .context import ModelContext
from .contracts import ModelAction, ModelOutput, ModelProtocol, ModelRunSummary, ModelStatus
from .tables import ensure_model_tables, record_model_run, replace_output_table


def check_model(
    db_path: Path,
    model: ModelProtocol,
    *,
    units_config: str | Path | None = None,
) -> ModelRunSummary:
    return _execute_model(db_path, model, action="check", units_config=units_config)


def run_model(
    db_path: Path,
    model: ModelProtocol,
    *,
    units_config: str | Path | None = None,
) -> ModelRunSummary:
    return _execute_model(db_path, model, action="run", units_config=units_config)


def _execute_model(
    db_path: Path,
    model: ModelProtocol,
    *,
    action: ModelAction,
    units_config: str | Path | None,
) -> ModelRunSummary:
    run_id = uuid.uuid4().hex
    with connect(db_path) as conn:
        ensure_model_tables(conn)
        ctx = ModelContext(
            conn, units=load_unit_registry(units_config), model_name=model.definition.name
        )
        ctx.require_endpoints(model.definition.required_endpoints)
        output: ModelOutput | None = None
        if not ctx.has_errors():
            if action == "check":
                model.check(ctx)
            else:
                output = model.run(ctx)
        row_count = len(output.rows) if output is not None else 0
        status = _status(ctx.has_errors(), ctx.issues)
        if action == "run" and output is not None and status != "failed":
            replace_output_table(
                conn,
                output_table=model.definition.output_table,
                output=output,
                run_id=run_id,
            )
        summary = ModelRunSummary(
            run_id=run_id,
            model_name=model.definition.name,
            title=model.definition.title,
            output_table=model.definition.output_table,
            action=action,
            status=status,
            row_count=row_count,
            issue_count=ctx.issue_count,
            error_count=ctx.error_count,
            warning_count=ctx.warning_count,
            issues=tuple(ctx.issues),
        )
        record_model_run(conn, summary)
        return summary


def _status(has_errors: bool, issues: list[object]) -> ModelStatus:
    if has_errors:
        return "failed"
    if issues:
        return "attention"
    return "ok"
