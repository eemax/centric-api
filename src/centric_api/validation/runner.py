from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..config import ConfigError, runtime_home
from ..store import connect_readonly
from ..units import load_unit_registry
from .artifacts import write_validation_artifacts
from .context import ValidationContext
from .contracts import (
    ValidationFinding,
    ValidationFindingTotals,
    ValidationResult,
    ValidationRunSummary,
    ValidationStatus,
    ValidatorProtocol,
)

DEFAULT_VALIDATION_RUNS_DIR = Path("validation/runs")


def run_validator(
    db_path: Path,
    validator: ValidatorProtocol,
    *,
    output_root: str | Path | None = None,
    units_config: str | Path | None = None,
) -> ValidationRunSummary:
    started_at = _utc_iso()
    run_id = _run_id(validator.definition.name)
    output_dir = _output_dir(validator.definition.name, run_id, output_root)
    try:
        with connect_readonly(db_path) as conn:
            ctx = ValidationContext(
                conn,
                units=load_unit_registry(units_config),
                validator_name=validator.definition.name,
                artifact_dir=output_dir,
            )
            for endpoint in validator.definition.required_endpoints:
                ctx.resolve_endpoint(endpoint)
            output_dir.mkdir(parents=True, exist_ok=True)
            result = validator.run(ctx)
        _validate_result(validator.definition.name, result)
        finished_at = _utc_iso()
        errors, warnings, info, total_findings = _finding_totals(result)
        finding_samples = _finding_samples(result)
        status = _status(errors, warnings, info)
        run_record = {
            "run_id": run_id,
            "validator": validator.definition.name,
            "title": validator.definition.title,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "findings": total_findings,
            "errors": errors,
            "warnings": warnings,
            "info": info,
        }
        report_path, summary_path, findings_path = write_validation_artifacts(
            output_dir,
            result,
            run_record=run_record,
        )
    except Exception:
        _remove_empty_output_dirs(output_dir)
        raise
    return ValidationRunSummary(
        run_id=run_id,
        validator_name=validator.definition.name,
        title=validator.definition.title,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        output_dir=output_dir,
        report_path=report_path,
        summary_path=summary_path,
        findings_path=findings_path,
        finding_count=total_findings,
        error_count=errors,
        warning_count=warnings,
        info_count=info,
        summary=result.summary,
        finding_samples=finding_samples,
    )


def _output_dir(validator_name: str, run_id: str, output_root: str | Path | None) -> Path:
    root = (
        Path(output_root).expanduser()
        if output_root is not None
        else runtime_home() / DEFAULT_VALIDATION_RUNS_DIR
    )
    return root / validator_name / run_id


def _remove_empty_output_dirs(output_dir: Path) -> None:
    for candidate in (output_dir, output_dir.parent):
        try:
            candidate.rmdir()
        except OSError:
            return


def _status(errors: int, warnings: int, info: int) -> ValidationStatus:
    if errors:
        return "failed"
    if warnings or info:
        return "attention"
    return "ok"


def _finding_totals(result: ValidationResult) -> tuple[int, int, int, int]:
    if result.finding_totals is not None:
        errors = int(result.finding_totals.errors)
        warnings = int(result.finding_totals.warnings)
        info = int(result.finding_totals.info)
        total = int(result.finding_totals.findings)
        return errors, warnings, info, total
    errors = sum(1 for finding in result.findings if finding.severity == "error")
    warnings = sum(1 for finding in result.findings if finding.severity == "warning")
    info = sum(1 for finding in result.findings if finding.severity == "info")
    return errors, warnings, info, len(result.findings)


def _finding_samples(result: ValidationResult) -> tuple:
    if result.finding_samples:
        return result.finding_samples
    return result.findings


def _validate_result(validator_name: str, result: object) -> None:
    if not isinstance(result, ValidationResult):
        raise ConfigError(f"Validator {validator_name} must return ValidationResult.")
    _validate_findings(validator_name, "findings", result.findings)
    _validate_findings(validator_name, "finding_samples", result.finding_samples)
    _validate_finding_totals(validator_name, result)
    if result.report_workbook is not None and not isinstance(result.report_workbook, bytes):
        raise ConfigError(f"Validator {validator_name} report_workbook must be bytes.")
    if result.findings_export_limit is not None and result.findings_export_limit < 0:
        raise ConfigError(
            f"Validator {validator_name} findings_export_limit must be zero or greater."
        )


def _validate_findings(
    validator_name: str,
    field_name: str,
    findings: tuple[ValidationFinding, ...],
) -> None:
    for finding in findings:
        if not isinstance(finding, ValidationFinding):
            raise ConfigError(
                f"Validator {validator_name} {field_name} must contain ValidationFinding items."
            )


def _validate_finding_totals(validator_name: str, result: ValidationResult) -> None:
    if result.finding_totals is None:
        if result.finding_samples and not result.findings:
            raise ConfigError(
                f"Validator {validator_name} with finding_samples must provide finding_totals."
            )
        return
    if not isinstance(result.finding_totals, ValidationFindingTotals):
        raise ConfigError(
            f"Validator {validator_name} finding_totals must be ValidationFindingTotals."
        )
    totals = result.finding_totals
    values = {
        "findings": _total_value(validator_name, "findings", totals.findings),
        "errors": _total_value(validator_name, "errors", totals.errors),
        "warnings": _total_value(validator_name, "warnings", totals.warnings),
        "info": _total_value(validator_name, "info", totals.info),
    }
    for key, value in values.items():
        if value < 0:
            raise ConfigError(f"Validator {validator_name} finding_totals.{key} must be >= 0.")
    if values["findings"] < values["errors"] + values["warnings"] + values["info"]:
        raise ConfigError(
            f"Validator {validator_name} finding_totals.findings cannot be smaller than "
            "errors + warnings + info."
        )
    sample_totals = ValidationFindingTotals.from_findings(_finding_samples(result))
    if (
        values["findings"] < sample_totals.findings
        or values["errors"] < sample_totals.errors
        or values["warnings"] < sample_totals.warnings
        or values["info"] < sample_totals.info
    ):
        raise ConfigError(
            f"Validator {validator_name} finding_totals cannot be smaller than exported samples."
        )


def _total_value(validator_name: str, field_name: str, value: object) -> int:
    try:
        total = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Validator {validator_name} finding_totals.{field_name} must be an integer."
        ) from exc
    return total


def _run_id(validator_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{_safe_name(validator_name)}-{uuid4().hex[:8]}"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value).strip("-")


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()
