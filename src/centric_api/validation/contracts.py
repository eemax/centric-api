from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

ValidationSeverity = Literal["error", "warning", "info"]
ValidationStatus = Literal["ok", "attention", "failed"]


@dataclass(frozen=True)
class ValidationDefinition:
    name: str
    title: str
    required_endpoints: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ValidationFinding:
    severity: ValidationSeverity
    code: str
    message: str
    endpoint: str | None = None
    record_id: str | None = None
    record_name: str | None = None
    style_id: str | None = None
    style_name: str | None = None
    brand: str | None = None
    season: str | None = None
    source_endpoint: str | None = None
    source_record_id: str | None = None
    source_field: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationSheet:
    name: str
    rows: tuple[dict[str, Any], ...]
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationFindingTotals:
    findings: int
    errors: int = 0
    warnings: int = 0
    info: int = 0

    @classmethod
    def from_findings(cls, findings: Iterable[ValidationFinding]) -> ValidationFindingTotals:
        errors = 0
        warnings = 0
        info = 0
        total = 0
        for finding in findings:
            total += 1
            if finding.severity == "error":
                errors += 1
            elif finding.severity == "warning":
                warnings += 1
            elif finding.severity == "info":
                info += 1
        return cls(findings=total, errors=errors, warnings=warnings, info=info)


@dataclass(frozen=True)
class ValidationResult:
    summary: dict[str, Any]
    findings: tuple[ValidationFinding, ...] = ()
    finding_samples: tuple[ValidationFinding, ...] = ()
    finding_totals: ValidationFindingTotals | None = None
    findings_export_limit: int | None = None
    sheets: tuple[ValidationSheet, ...] = ()
    report_workbook: bytes | None = None


@dataclass(frozen=True)
class ValidationRunSummary:
    run_id: str
    validator_name: str
    title: str
    status: ValidationStatus
    started_at: str
    finished_at: str
    output_dir: Path
    report_path: Path
    summary_path: Path
    findings_path: Path
    finding_count: int
    error_count: int
    warning_count: int
    info_count: int
    summary: dict[str, Any]
    finding_samples: tuple[ValidationFinding, ...]


class ValidatorProtocol(Protocol):
    definition: ValidationDefinition

    def run(self, ctx: Any) -> ValidationResult: ...
