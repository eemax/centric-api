from __future__ import annotations

from typing import Any

from ..modeling.contracts import ModelProtocol, ModelRunSummary
from ..modeling.tables import issue_record
from .common import format_count


def model_record(model: ModelProtocol) -> dict[str, Any]:
    definition = model.definition
    return {
        "name": definition.name,
        "title": definition.title,
        "output_table": definition.output_table,
        "required_endpoints": list(definition.required_endpoints),
        "description": definition.description,
    }


def summary_record(summary: ModelRunSummary) -> dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "model": summary.model_name,
        "title": summary.title,
        "output_table": summary.output_table,
        "action": summary.action,
        "status": summary.status,
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "rows": summary.row_count,
        "issues": summary.issue_count,
        "errors": summary.error_count,
        "warnings": summary.warning_count,
        "metrics": summary.metrics or {},
        "issue_details": [issue_record(issue) for issue in summary.issues],
    }


def print_human_model_list(models: tuple[ModelProtocol, ...]) -> None:
    print("Models")
    print()
    print(f"Models: {format_count(len(models))}")
    if not models:
        return
    print()
    name_width = max(len("Name"), *(len(model.definition.name) for model in models))
    table_width = max(len("Output"), *(len(model.definition.output_table) for model in models))
    header = f"{'Name':<{name_width}}  {'Output':<{table_width}}  Title"
    print(header)
    print("-" * len(header))
    for model in models:
        print(
            f"{model.definition.name:<{name_width}}  "
            f"{model.definition.output_table:<{table_width}}  "
            f"{model.definition.title}"
        )


def print_human_model_show(model: ModelProtocol) -> None:
    definition = model.definition
    print(f"Model: {definition.name}")
    print()
    print(f"Title:  {definition.title}")
    print(f"Output: {definition.output_table}")
    if definition.description:
        print(f"About:  {definition.description}")
    if definition.required_endpoints:
        print(f"Needs:  {', '.join(definition.required_endpoints)}")


def print_human_model_summary(summary: ModelRunSummary) -> None:
    label = "Model check" if summary.action == "check" else "Model run"
    print(f"{label}: {summary.model_name}")
    print()
    print(f"Status:   {summary.status}")
    print(f"Run:      {summary.run_id}")
    print(f"Output:   {summary.output_table}")
    if summary.action == "run":
        print(f"Rows:     {format_count(summary.row_count)}")
    print(f"Issues:   {format_count(summary.issue_count)}")
    print(f"Errors:   {format_count(summary.error_count)}")
    print(f"Warnings: {format_count(summary.warning_count)}")
    _print_metrics(summary)
    _print_issues(summary)


def _print_metrics(summary: ModelRunSummary) -> None:
    if not summary.metrics:
        return
    print()
    print("Metrics")
    for key, value in summary.metrics.items():
        label = key.replace("_", " ").title()
        if isinstance(value, int):
            value = format_count(value)
        print(f"  {label}: {value}")


def _print_issues(summary: ModelRunSummary) -> None:
    if not summary.issues:
        return
    print()
    print("Issue Samples")
    for issue in summary.issues[:10]:
        location = ""
        if issue.endpoint and issue.record_id:
            location = f" [{issue.endpoint}:{issue.record_id}]"
        elif issue.endpoint:
            location = f" [{issue.endpoint}]"
        print(f"  {issue.severity.upper()} {issue.code}{location}: {issue.message}")
    hidden_count = summary.issue_count - min(len(summary.issues), 10)
    if hidden_count > 0:
        print(f"  ... {hidden_count} more issue{'' if hidden_count == 1 else 's'}")
