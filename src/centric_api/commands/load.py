from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..auth import init_auth_context
from ..config import LOCAL_ENV_CONFIG_PATH, ConfigError, resolve_private_config_path
from ..defaults import db_path as resolve_db_path
from ..load import (
    RETRY_STATUSES,
    materialize_load,
    materialize_material_create_with_composition_and_quote_workflow,
    materialize_material_create_with_composition_workflow,
    materialize_material_supplier_quote_workflow,
    materialize_style_bom_workflow,
    materialize_style_supplier_quote_workflow,
    run_load,
    run_material_create_with_composition_and_quote_workflow,
    run_material_create_with_composition_workflow,
    run_material_supplier_quote_workflow,
    run_style_bom_workflow,
    run_style_supplier_quote_workflow,
)
from ..load_config import load_load_config, select_load_job
from ..models import AuthSettings
from ..rendering.common import print_rows
from ..rendering.load import (
    check_record,
    load_job_record,
    print_human_load_check,
    print_human_load_list,
    print_human_load_run,
    print_human_load_show,
    result_record,
    write_load_progress_line,
)


def run_load_command(args: argparse.Namespace) -> int:
    config = load_load_config(args.load_config)
    if args.action == "list":
        rows = [load_job_record(job) for job in config.jobs]
        if args.json:
            return print_rows(rows, True, empty_message="No load jobs found.")
        print_human_load_list(config.jobs)
        return 0

    job = select_load_job(config, args.name)
    if args.action == "show":
        payload = load_job_record(job)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print_human_load_show(job)
        return 0

    workbook_path = Path(args.workbook).expanduser()
    progress_callback = None if args.json or args.quiet else write_load_progress_line
    if args.action == "check":
        materializer = _workflow_materializer(job.workflow)
        result = materializer(
            resolve_db_path(args.db),
            job,
            workbook_path,
            sheet=args.sheet,
            limit=args.limit,
            progress_callback=progress_callback,
        )
        if args.json:
            print(json.dumps(check_record(result), default=str))
        else:
            print_human_load_check(result)
        return 0 if not result.issues else 1

    dry_run = bool(args.dry_run)
    retry_statuses = _parse_retry_statuses(args.statuses) if args.action == "retry" else None
    runner = _workflow_runner(job.workflow)
    materializer = _workflow_materializer(job.workflow)
    if dry_run:
        result = runner(
            resolve_db_path(args.db),
            config,
            job,
            workbook_path,
            sheet=args.sheet,
            limit=args.limit,
            dry_run=True,
            yes=args.yes,
            retry_statuses=retry_statuses,
            progress_callback=progress_callback,
        )
    else:
        if not args.yes:
            raise ConfigError("Non-dry-run load requires --yes.")
        mode = "retry" if retry_statuses else "run"
        materialized = materializer(
            resolve_db_path(args.db),
            job,
            workbook_path,
            sheet=args.sheet,
            limit=args.limit,
            mode=mode,
            retry_statuses=retry_statuses,
            progress_callback=progress_callback,
        )
        if materialized.requests:
            auth_settings = AuthSettings(
                env_file=resolve_private_config_path(LOCAL_ENV_CONFIG_PATH, args.env_file)
            )
            with init_auth_context(auth_settings) as auth_ctx:
                result = runner(
                    resolve_db_path(args.db),
                    config,
                    job,
                    workbook_path,
                    sheet=args.sheet,
                    limit=args.limit,
                    dry_run=False,
                    yes=args.yes,
                    retry_statuses=retry_statuses,
                    materialized=materialized,
                    auth_ctx=auth_ctx,
                    progress_callback=progress_callback,
                )
        else:
            result = runner(
                resolve_db_path(args.db),
                config,
                job,
                workbook_path,
                sheet=args.sheet,
                limit=args.limit,
                dry_run=False,
                yes=args.yes,
                retry_statuses=retry_statuses,
                materialized=materialized,
                progress_callback=progress_callback,
            )
    if args.json:
        print(json.dumps(result_record(result), default=str))
    else:
        print_human_load_run(result)
    return 0 if result.failure_count == 0 and not result.issues else 1


def _parse_retry_statuses(value: str | None) -> set[str]:
    if value is None:
        return set(RETRY_STATUSES)
    statuses = {_normalize_status(item) for item in value.split(",") if item.strip()}
    unknown = statuses - RETRY_STATUSES
    if unknown:
        choices = ", ".join(sorted(RETRY_STATUSES))
        raise ValueError(f"Retry statuses must be one of: {choices}.")
    if not statuses:
        raise ValueError("Retry statuses cannot be empty.")
    return statuses


def _workflow_materializer(workflow: str):
    if workflow == "material_create_with_composition_and_quote":
        return materialize_material_create_with_composition_and_quote_workflow
    if workflow == "material_create_with_composition":
        return materialize_material_create_with_composition_workflow
    if workflow == "material_supplier_quote":
        return materialize_material_supplier_quote_workflow
    if workflow == "style_bom":
        return materialize_style_bom_workflow
    if workflow == "style_supplier_quote":
        return materialize_style_supplier_quote_workflow
    return materialize_load


def _workflow_runner(workflow: str):
    if workflow == "material_create_with_composition_and_quote":
        return run_material_create_with_composition_and_quote_workflow
    if workflow == "material_create_with_composition":
        return run_material_create_with_composition_workflow
    if workflow == "material_supplier_quote":
        return run_material_supplier_quote_workflow
    if workflow == "style_bom":
        return run_style_bom_workflow
    if workflow == "style_supplier_quote":
        return run_style_supplier_quote_workflow
    return run_load


def _normalize_status(value: str) -> str:
    return value.strip().casefold()
