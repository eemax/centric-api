from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from centric_api.store import connect


def _write_style_name_validator(path: Path) -> None:
    path.write_text(
        """
from centric_api.validation import (
    ValidationDefinition,
    ValidationFinding,
    ValidationResult,
    ValidationSheet,
)


class StyleNameValidator:
    definition = ValidationDefinition(
        name="style-name-check",
        title="Style Name Check",
        required_endpoints=("styles",),
        description="Checks that cached styles have names.",
    )

    def run(self, ctx):
        findings = []
        rows = []
        for style in ctx.records("styles"):
            style_id = style.get("id")
            style_name = style.get("node_name")
            if not style_name:
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="STYLE_NAME_MISSING",
                        message="Style is missing node_name.",
                        endpoint="styles",
                        record_id=style_id,
                    )
                )
            rows.append(
                {
                    "style_id": style_id,
                    "style_name": style_name,
                    "status": "ok" if style_name else "error",
                }
            )
        return ValidationResult(
            summary={
                "styles": len(rows),
                "styles_missing_name": len(findings),
                "mode": ctx.mode,
                "input_file": str(ctx.input_file) if ctx.input_file else None,
                "artifact_timestamp": ctx.artifact_timestamp,
                "value_sets": {
                    "Product Group": {
                        "path": "/tmp/styles.custom_style_product_group.xlsx",
                        "found": True,
                        "validation": "enabled",
                        "value_count": 104,
                    }
                },
            },
            findings=tuple(findings),
            sheets=(ValidationSheet("Styles", tuple(rows)),),
        )


VALIDATOR = StyleNameValidator()
""",
        encoding="utf-8",
    )


def _write_sampled_validator(path: Path) -> None:
    path.write_text(
        """
from centric_api.validation import (
    ValidationDefinition,
    ValidationFinding,
    ValidationFindingTotals,
    ValidationResult,
)


class SampledValidator:
    definition = ValidationDefinition(
        name="sampled-check",
        title="Sampled Check",
        required_endpoints=("styles",),
    )

    def run(self, ctx):
        return ValidationResult(
            summary={"styles": len(ctx.records("styles"))},
            finding_samples=(
                ValidationFinding(
                    severity="error",
                    code="SAMPLE",
                    message="Sample finding.",
                    endpoint="styles",
                    record_id="S1",
                ),
            ),
            finding_totals=ValidationFindingTotals(
                findings=100,
                errors=90,
                warnings=10,
            ),
        )


VALIDATOR = SampledValidator()
""",
        encoding="utf-8",
    )


def _write_invalid_totals_validator(path: Path) -> None:
    path.write_text(
        """
from centric_api.validation import (
    ValidationDefinition,
    ValidationFinding,
    ValidationFindingTotals,
    ValidationResult,
)


class InvalidTotalsValidator:
    definition = ValidationDefinition(
        name="invalid-totals",
        title="Invalid Totals",
        required_endpoints=("styles",),
    )

    def run(self, ctx):
        return ValidationResult(
            summary={},
            finding_samples=(
                ValidationFinding(
                    severity="error",
                    code="SAMPLE",
                    message="Sample finding.",
                    endpoint="styles",
                    record_id="S1",
                ),
            ),
            finding_totals=ValidationFindingTotals(findings=1, errors=0),
        )


VALIDATOR = InvalidTotalsValidator()
""",
        encoding="utf-8",
    )


def _write_invalid_report_workbook_validator(path: Path) -> None:
    path.write_text(
        """
from centric_api.validation import (
    ValidationDefinition,
    ValidationResult,
)


class InvalidReportWorkbookValidator:
    definition = ValidationDefinition(
        name="invalid-report-workbook",
        title="Invalid Report Workbook",
        required_endpoints=("styles",),
    )

    def run(self, ctx):
        return ValidationResult(
            summary={},
            report_workbook="not bytes",
        )


VALIDATOR = InvalidReportWorkbookValidator()
""",
        encoding="utf-8",
    )


def _write_invalid_history_metric_validator(path: Path) -> None:
    path.write_text(
        """
from centric_api.validation import (
    ValidationDefinition,
    ValidationHistoryMetric,
    ValidationResult,
)


class InvalidHistoryMetricValidator:
    definition = ValidationDefinition(
        name="invalid-history-metric",
        title="Invalid History Metric",
        required_endpoints=("styles",),
    )

    def run(self, ctx):
        return ValidationResult(
            summary={},
            history_metrics=(
                ValidationHistoryMetric(
                    metric="Style Completion %",
                    value=float("nan"),
                    unit="percent",
                    trend="up",
                    scope="overall",
                ),
            ),
        )


VALIDATOR = InvalidHistoryMetricValidator()
""",
        encoding="utf-8",
    )


def _write_invalid_history_metric_trend_validator(path: Path) -> None:
    path.write_text(
        """
from centric_api.validation import (
    ValidationDefinition,
    ValidationHistoryMetric,
    ValidationResult,
)


class InvalidHistoryTrendValidator:
    definition = ValidationDefinition(
        name="invalid-history-trend",
        title="Invalid History Trend",
        required_endpoints=("styles",),
    )

    def run(self, ctx):
        return ValidationResult(
            summary={},
            history_metrics=(
                ValidationHistoryMetric(
                    metric="Active Styles",
                    value=1,
                    unit="count",
                    trend="sideways",
                    scope="overall",
                ),
            ),
        )


VALIDATOR = InvalidHistoryTrendValidator()
""",
        encoding="utf-8",
    )


def _seed_styles_cache(db_path: Path) -> None:
    with connect(db_path) as conn:
        _insert_record(conn, "styles", "S1", {"id": "S1", "node_name": "Style One"})
        _insert_record(conn, "styles", "S2", {"id": "S2"})


def _insert_record(
    conn: sqlite3.Connection,
    endpoint: str,
    record_id: str,
    payload: dict[str, object],
) -> None:
    conn.execute(
        """
        INSERT INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            record_id,
            json.dumps(payload, sort_keys=True),
            f"hash-{endpoint}-{record_id}",
            None,
            f"{endpoint}.jsonl",
            "run-1",
            "2026-01-01T00:00:00Z",
        ],
    )
