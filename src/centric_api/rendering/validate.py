from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..validation.contracts import ValidationRunSummary, ValidatorProtocol
from .common import format_count


def validator_record(validator: ValidatorProtocol) -> dict[str, Any]:
    definition = validator.definition
    return {
        "name": definition.name,
        "title": definition.title,
        "required_endpoints": list(definition.required_endpoints),
        "description": definition.description,
    }


def validation_summary_record(summary: ValidationRunSummary) -> dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "validator": summary.validator_name,
        "title": summary.title,
        "run_status": "completed",
        "validation_outcome": summary.status,
        "status": summary.status,
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "output_dir": str(summary.output_dir),
        "report_path": str(summary.report_path),
        "summary_path": str(summary.summary_path),
        "findings_path": str(summary.findings_path),
        "history_path": str(summary.history_path),
        "findings": summary.finding_count,
        "blocking": summary.error_count,
        "errors": summary.error_count,
        "warnings": summary.warning_count,
        "info": summary.info_count,
        "summary": summary.summary,
        "finding_samples": [asdict(finding) for finding in summary.finding_samples[:10]],
    }


def print_human_validator_list(validators: tuple[ValidatorProtocol, ...]) -> None:
    print("Validators")
    print()
    print(f"Validators: {format_count(len(validators))}")
    if not validators:
        return
    print()
    name_width = max(len("Name"), *(len(validator.definition.name) for validator in validators))
    header = f"{'Name':<{name_width}}  Title"
    print(header)
    print("-" * len(header))
    for validator in validators:
        print(f"{validator.definition.name:<{name_width}}  {validator.definition.title}")


def print_human_validator_show(validator: ValidatorProtocol) -> None:
    definition = validator.definition
    print(f"Validator: {definition.name}")
    print()
    print(f"Title: {definition.title}")
    if definition.description:
        print(f"About: {definition.description}")
    if definition.required_endpoints:
        print(f"Needs: {', '.join(definition.required_endpoints)}")


def print_human_validation_summary(summary: ValidationRunSummary) -> None:
    print(f"Validation: {summary.validator_name}")
    print()
    print("Run:      completed")
    print(f"Outcome:  {summary.status}")
    print(f"ID:       {summary.run_id}")
    print(f"Report:   {summary.report_path}")
    print(f"Findings: {summary.findings_path}")
    print(f"Summary:  {summary.summary_path}")
    print(f"History:  {summary.history_path}")
    print()
    print("Findings")
    print(f"Total:    {format_count(summary.finding_count)}")
    print(f"Blocking: {format_count(summary.error_count)}")
    print(f"Warnings: {format_count(summary.warning_count)}")
    print(f"Info:     {format_count(summary.info_count)}")
    _print_summary_metrics(summary)
    _print_finding_samples(summary)


def _print_summary_metrics(summary: ValidationRunSummary) -> None:
    if not summary.summary:
        return
    rows = [
        (key, value)
        for key, value in summary.summary.items()
        if key not in {"findings", "errors", "warnings", "info"}
    ]
    if not rows:
        return
    print()
    print("Metrics")
    for key, value in rows:
        label = key.replace("_", " ").title()
        if isinstance(value, int):
            value = format_count(value)
        print(f"  {label}: {value}")


def _print_finding_samples(summary: ValidationRunSummary) -> None:
    if not summary.finding_samples:
        return
    print()
    print("Finding Samples")
    for finding in summary.finding_samples[:10]:
        location = ""
        if finding.style_id:
            location = f" [{finding.style_id}]"
        elif finding.endpoint and finding.record_id:
            location = f" [{finding.endpoint}:{finding.record_id}]"
        severity = _severity_label(finding.severity)
        print(f"  {severity} {finding.code}{location}: {finding.message}")
    hidden_count = summary.finding_count - min(len(summary.finding_samples), 10)
    if hidden_count > 0:
        print(f"  ... {hidden_count} more finding{'' if hidden_count == 1 else 's'}")


def _severity_label(severity: str) -> str:
    if severity == "error":
        return "BLOCKING"
    if severity == "warning":
        return "WARNING"
    return severity.upper()
