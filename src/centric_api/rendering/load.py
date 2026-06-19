from __future__ import annotations

import json
import sys
from typing import Any

from ..load import MAX_SAMPLES, LoadMaterialized, LoadRunResult, materialized_record, run_record
from ..load_config import LoadJob
from .common import format_count
from .logs import format_seconds


def load_job_record(job: LoadJob) -> dict[str, Any]:
    return {
        "name": job.name,
        "title": job.title,
        "source": job.source,
        "source_path": str(job.source_path),
        "method": job.method,
        "path": job.path,
        "workflow": job.workflow,
        "header_row": job.input.header_row,
        "columns": [
            {
                "key": column.key,
                "header": column.header,
                "headers": list(column.headers),
                "type": column.type,
                "required": column.required,
                "resolve": (
                    {
                        "endpoint": column.resolve.endpoint,
                        "match": column.resolve.match,
                        "output": column.resolve.output,
                        "filters": column.resolve.filters or {},
                        "scope": (
                            {
                                "column": column.resolve.scope.column,
                                "endpoint": column.resolve.scope.endpoint,
                                "via": column.resolve.scope.via,
                                "match": column.resolve.scope.match,
                                "output": column.resolve.scope.output,
                            }
                            if column.resolve.scope
                            else None
                        ),
                    }
                    if column.resolve
                    else None
                ),
                "value_set": ({"name": column.value_set.name} if column.value_set else None),
            }
            for column in job.columns
        ],
        "body": job.body,
    }


def write_load_progress_line(event: dict[str, Any]) -> None:
    if event.get("event") == "load_planning":
        print(
            f"[load] planning: job={event.get('job')} mode={event.get('mode')} "
            f"workbook={event.get('workbook')} sheet={event.get('sheet')}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "load_headers":
        print(
            f"[load] headers: matched={event.get('matched')}/{event.get('columns')} "
            f"required={event.get('required_matched')}/{event.get('required')} "
            f"aliases={event.get('aliases')} issues={event.get('issues')}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "load_refs":
        filters = _format_filters(event.get("filters"))
        suffix = f" filter={filters}" if filters else ""
        print(
            f"[load] refs: {event.get('endpoint')} matched={event.get('matched')} "
            f"values={event.get('values')}{suffix}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "load_values":
        print(
            f"[load] values: {event.get('name')} values={event.get('values')} "
            f"file={event.get('path')}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "load_validate":
        print(
            f"[load] validate: scanned={event.get('scanned')} valid={event.get('valid')} "
            f"errors={event.get('errors')}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "load_artifacts":
        print(
            f"[load] artifacts: {event.get('run_dir')} requests={event.get('requests')}",
            file=sys.stderr,
        )
        return
    if event.get("event") == "load_send":
        print(
            f"[load] send: {event.get('index')}/{event.get('total')} "
            f"status={event.get('status_code')} row={event.get('row')} "
            f"elapsed={format_seconds(event.get('elapsed_seconds'))}",
            file=sys.stderr,
        )


def print_human_load_list(jobs: tuple[LoadJob, ...]) -> None:
    print("Load Jobs")
    print()
    print(f"Jobs: {format_count(len(jobs))}")
    if not jobs:
        return
    print()
    name_width = max(len("Name"), *(len(job.name) for job in jobs))
    source_width = max(len("Source"), *(len(job.source) for job in jobs))
    method_width = max(len("Method"), *(len(job.method) for job in jobs))
    header = f"{'Name':<{name_width}}  {'Source':<{source_width}}  {'Method':<{method_width}}  Path"
    print(header)
    print("-" * len(header))
    for job in jobs:
        print(
            f"{job.name:<{name_width}}  {job.source:<{source_width}}  "
            f"{job.method:<{method_width}}  {job.path}"
        )


def print_human_load_show(job: LoadJob) -> None:
    print(f"Load job: {job.name}")
    print()
    print(f"Title:      {job.title}")
    print(f"Source:     {job.source}")
    print(f"Config:     {job.source_path}")
    print(f"Method:     {job.method}")
    print(f"Path:       {job.path}")
    print(f"Workflow:   {job.workflow}")
    print(f"Header row: {job.input.header_row}")
    print()
    print("Columns")
    key_width = max(len("Key"), *(len(column.key) for column in job.columns))
    type_width = max(len("Type"), *(len(column.type) for column in job.columns))
    header = f"  {'Key':<{key_width}}  {'Type':<{type_width}}  Required  Header"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for column in job.columns:
        required = "yes" if column.required else "no"
        aliases = f" ({', '.join(column.headers)})" if column.headers else ""
        print(
            f"  {column.key:<{key_width}}  {column.type:<{type_width}}  "
            f"{required:<8}  {column.header}{aliases}"
        )
        if column.resolve:
            filters = f" filters={column.resolve.filters}" if column.resolve.filters else ""
            print(
                f"  {'':<{key_width}}  {'':<{type_width}}  {'':<8}  "
                f"resolves {column.resolve.endpoint}.{column.resolve.match} -> "
                f"{column.resolve.output}{filters}"
            )
            if column.resolve.scope:
                scope = column.resolve.scope
                print(
                    f"  {'':<{key_width}}  {'':<{type_width}}  {'':<8}  "
                    f"scope {scope.column} via {column.resolve.endpoint}.{scope.via} -> "
                    f"{scope.endpoint}.{scope.match}"
                )
        if column.value_set:
            print(
                f"  {'':<{key_width}}  {'':<{type_width}}  {'':<8}  values {column.value_set.name}"
            )
    print()
    print("Body")
    if isinstance(job.body, str):
        print(f"  {job.body}")
    else:
        for target, source in job.body.items():
            print(f"  {target}: {source}")


def print_human_load_check(result: LoadMaterialized) -> None:
    print(f"Load check: {result.job_name}")
    print()
    print(f"Workbook:     {result.workbook_path}")
    print(f"Sheet:        {result.sheet}")
    print(f"Rows scanned: {format_count(result.rows_scanned)}")
    print(f"Valid rows:   {format_count(result.valid_rows)}")
    print(f"Error rows:   {format_count(result.error_rows)}")
    print(f"Status:       {'ok' if not result.issues else 'attention needed'}")
    _print_issues(result.issues)
    _print_request_samples(result.requests)


def print_human_load_run(result: LoadRunResult) -> None:
    label = _run_label(result)
    print(f"{label}: {result.job_name}")
    print()
    print(f"Run:          {result.run_id}")
    print(f"Workbook:     {result.workbook_path}")
    print(f"Sheet:        {result.sheet}")
    print(f"Rows scanned: {format_count(result.rows_scanned)}")
    print(f"Requests:     {format_count(result.request_count)}")
    if not result.dry_run:
        print(f"Successes:    {format_count(result.success_count)}")
        print(f"Failures:     {format_count(result.failure_count)}")
    print(f"Run dir:      {result.run_dir}")
    if result.review_path:
        print(f"Review file:  {result.review_path}")
    _print_issues(result.issues)
    _print_request_samples(result.requests)


def check_record(result: LoadMaterialized) -> dict[str, Any]:
    payload = materialized_record(result)
    payload["ok"] = not result.issues
    return payload


def result_record(result: LoadRunResult) -> dict[str, Any]:
    payload = run_record(result)
    payload["ok"] = result.failure_count == 0 and not result.issues
    return payload


def _run_label(result: LoadRunResult) -> str:
    if result.mode == "retry-dry-run":
        return "Load retry dry run"
    if result.mode == "retry":
        return "Load retry"
    if result.dry_run:
        return "Load dry run"
    return "Load run"


def _print_issues(issues: tuple[Any, ...]) -> None:
    if not issues:
        return
    print()
    print("Issues")
    for issue in issues[:10]:
        row = f" row {issue.row}" if issue.row is not None else ""
        column = f" [{issue.column}]" if issue.column else ""
        print(f"  {issue.code}{row}{column}: {issue.message}")
    hidden_count = len(issues) - 10
    if hidden_count > 0:
        print(f"  ... {hidden_count} more issue{'' if hidden_count == 1 else 's'}")


def _print_request_samples(requests: tuple[Any, ...]) -> None:
    if not requests:
        return
    print()
    print("Request Samples")
    for request in requests[:MAX_SAMPLES]:
        print(f"  row {request.row}: {request.method} {request.path} {request.body}")
    hidden_count = len(requests) - MAX_SAMPLES
    if hidden_count > 0:
        print(f"  ... {hidden_count} more request{'' if hidden_count == 1 else 's'}")


def _format_filters(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return ",".join(
        f"{key}:{json.dumps(value[key], default=str, sort_keys=True)}" for key in sorted(value)
    )
