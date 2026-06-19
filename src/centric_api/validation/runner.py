from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from ..artifact_names import allocate_artifact_dir, artifact_slug
from ..config import ConfigError, runtime_home
from ..store import connect_readonly
from ..units import load_unit_registry
from .artifacts import validation_artifact_timestamp, write_validation_artifacts
from .context import ValidationContext
from .contracts import (
    ValidationFinding,
    ValidationFindingTotals,
    ValidationHistoryMetric,
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
    mode: str = "cache",
    input_file: str | Path | None = None,
) -> ValidationRunSummary:
    started_at = _utc_iso()
    artifact_timestamp = validation_artifact_timestamp(started_at)
    run_id, output_dir = _allocate_output_dir(
        validator.definition.name,
        started_at=started_at,
        output_root=output_root,
    )
    try:
        with connect_readonly(db_path) as conn:
            ctx = ValidationContext(
                conn,
                units=load_unit_registry(units_config),
                validator_name=validator.definition.name,
                artifact_dir=output_dir,
                artifact_timestamp=artifact_timestamp,
                mode=mode,
                input_file=input_file,
            )
            for endpoint in validator.definition.required_endpoints:
                ctx.resolve_endpoint(endpoint)
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
            "artifact_timestamp": artifact_timestamp,
            "findings": total_findings,
            "errors": errors,
            "warnings": warnings,
            "info": info,
        }
        report_path, summary_path, findings_path, history_path = write_validation_artifacts(
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
        history_path=history_path,
        finding_count=total_findings,
        error_count=errors,
        warning_count=warnings,
        info_count=info,
        summary=result.summary,
        finding_samples=finding_samples,
    )


def _allocate_output_dir(
    validator_name: str,
    *,
    started_at: str,
    output_root: str | Path | None,
) -> tuple[str, Path]:
    root = _output_root(output_root)
    try:
        return allocate_artifact_dir(
            root / artifact_slug(validator_name),
            validator_name,
            started_at,
        )
    except RuntimeError as exc:
        raise ConfigError(
            f"Unable to allocate validation output directory for {validator_name}."
        ) from exc


def _output_root(output_root: str | Path | None) -> Path:
    if output_root is not None:
        return Path(output_root).expanduser()
    return runtime_home() / DEFAULT_VALIDATION_RUNS_DIR


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
    _validate_history_metrics(validator_name, result.history_metrics)
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


def _validate_history_metrics(
    validator_name: str,
    metrics: tuple[ValidationHistoryMetric, ...],
) -> None:
    for metric in metrics:
        if not isinstance(metric, ValidationHistoryMetric):
            raise ConfigError(
                f"Validator {validator_name} history_metrics must contain "
                "ValidationHistoryMetric items."
            )
        if not metric.metric.strip():
            raise ConfigError(f"Validator {validator_name} history metric name is required.")
        if not isinstance(metric.value, int | float) or isinstance(metric.value, bool):
            raise ConfigError(
                f"Validator {validator_name} history metric {metric.metric} value must be numeric."
            )
        if not isfinite(float(metric.value)):
            raise ConfigError(
                f"Validator {validator_name} history metric {metric.metric} value must be finite."
            )
        _validate_optional_history_number(
            validator_name,
            metric.metric,
            "numerator",
            metric.numerator,
        )
        _validate_optional_history_number(
            validator_name,
            metric.metric,
            "denominator",
            metric.denominator,
        )
        if metric.unit not in {"percent", "count", "number"}:
            raise ConfigError(
                f"Validator {validator_name} history metric {metric.metric} unit is invalid."
            )
        if metric.trend not in {"up", "down", "neutral"}:
            raise ConfigError(
                f"Validator {validator_name} history metric {metric.metric} trend is invalid."
            )
        if not metric.scope.strip():
            raise ConfigError(f"Validator {validator_name} history metric scope is required.")
        if not isinstance(metric.dimensions, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metric.dimensions.items()
        ):
            raise ConfigError(
                f"Validator {validator_name} history metric {metric.metric} "
                "dimensions must be string keys and values."
            )


def _validate_optional_history_number(
    validator_name: str,
    metric_name: str,
    field_name: str,
    value: int | float | None,
) -> None:
    if value is None:
        return
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigError(
            f"Validator {validator_name} history metric {metric_name} {field_name} must be numeric."
        )
    if not isfinite(float(value)):
        raise ConfigError(
            f"Validator {validator_name} history metric {metric_name} {field_name} must be finite."
        )


def _total_value(validator_name: str, field_name: str, value: object) -> int:
    try:
        total = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Validator {validator_name} finding_totals.{field_name} must be an integer."
        ) from exc
    return total


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()
