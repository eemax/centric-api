# Validation

`centric-api validate` is a private extension host for cache validation and reporting. The main repo
provides the command surface, cache helpers, and standard artifact writer. The validation logic
itself lives outside the repo in Python modules.

```bash
centric-api validate list
centric-api validate show my-validator
centric-api validate run my-validator
centric-api validate run all
```

There are no bundled validators. By default, validator modules are loaded from:

```text
CENTRIC_API_HOME/validators/*.py
```

Use `--validators-dir PATH` to load from another directory.

Each run writes a timestamped artifact folder:

```text
CENTRIC_API_HOME/validation/runs/<validator>/<run-id>/
  report.xlsx
  summary.json
  findings.json
  history.json
```

Use `--output-dir PATH` to choose a different artifact root. The command still creates
`<validator>/<run-id>` below that root. Private validators can also write extra files into
`ctx.artifact_dir`.

## Validator Modules

Each `.py` file exposes `VALIDATOR` or `get_validator()`:

```python
from centric_api.validation import (
    ValidationDefinition,
    ValidationFinding,
    ValidationFindingTotals,
    ValidationHistoryMetric,
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
            },
            findings=tuple(findings),
            sheets=(ValidationSheet("Styles", tuple(rows)),),
        )


VALIDATOR = StyleNameValidator()
```

`ValidationResult.summary` becomes the metrics section in CLI output and `summary.json`.
For ordinary validators, return every generic finding in `ValidationResult.findings`; those
records become `findings.json`, the workbook `Findings` sheet, and CLI finding samples. Each
`ValidationSheet` becomes one additional workbook sheet before `Findings`.

`ValidationResult.history_metrics` is the machine-readable history contract. The runner writes
these metrics to `history.json` for every run. History is not inferred from `report.xlsx`; validators
that should appear in trends must publish explicit metrics:

```python
return ValidationResult(
    summary={"styles_checked": len(styles)},
    history_metrics=(
        ValidationHistoryMetric(
            metric="Style Completion %",
            value=ready_styles / len(styles) * 100,
            unit="percent",
            numerator=ready_styles,
            denominator=len(styles),
        ),
        ValidationHistoryMetric(
            metric="Active Styles",
            value=len(styles),
            unit="count",
        ),
    ),
)
```

For per-brand history, set `scope="brand"` and `brand="BRAND NAME"`. The generic history command
does not care which metrics a validator emits; it groups by validator, metric, scope, and brand.

### History Metric Authoring

Publish one `ValidationHistoryMetric` per trend line you want to preserve. Multiple metrics are
expected; a validator can emit both high-level completion percentages and supporting count metrics in
the same run. The history builder groups points by this identity:

```text
validator + metric + unit + scope + brand + time bucket
```

Within one time bucket, such as one week, the latest run wins for each identity. Keep these fields
stable across releases:

- `metric`: human-readable series name, for example `Style Completion %` or `Active Styles`.
- `unit`: use `percent` for percentages, `count` for integer counts, and `number` for other numeric
  values.
- `scope`: use `overall` for all-record metrics and `brand` for brand-specific metrics.
- `brand`: set only for brand-scoped metrics.
- `numerator` and `denominator`: include them for percentages so future reports can explain the
  exact ratio behind the percentage.
- `dimensions`: optional string metadata for later filtering. Keep it sparse and low-cardinality.

Avoid one metric per row, style, material, supplier, or issue. History metrics should be aggregated
signals that make sense over time. A good readiness validator usually emits:

- one or two percentage metrics, such as style completion and material completion
- supporting counts for active, ready, failed, and issue-bearing records
- both overall and per-brand versions of the same metric set when brand comparison matters

Prefer helper functions that build the overall metrics first, then loop over the same grouped data
for per-brand metrics:

```python
def history_metrics(results):
    metrics = readiness_metrics(results)
    for brand, brand_results in results_by_brand(results).items():
        metrics.extend(readiness_metrics(tuple(brand_results), brand=brand))
    return tuple(metrics)


def readiness_metrics(results, *, brand=None):
    scope = "brand" if brand else "overall"
    active = len(results)
    ready = sum(1 for result in results if result.ready)
    return [
        ValidationHistoryMetric(
            metric="Style Completion %",
            value=round((ready / active) * 100, 2) if active else 0.0,
            unit="percent",
            scope=scope,
            brand=brand,
            numerator=ready,
            denominator=active,
        ),
        ValidationHistoryMetric(
            metric="Active Styles",
            value=active,
            unit="count",
            scope=scope,
            brand=brand,
        ),
    ]
```

The runner validates that metric values, numerators, and denominators are finite numbers. Invalid
history metrics fail the validation run before artifacts are written.

For heavy validators, avoid constructing or exporting hundreds of thousands of generic finding
objects when a private report is the real artifact. Return a small `finding_samples` tuple plus
exact `finding_totals` instead:

```python
return ValidationResult(
    summary={"styles_checked": 18615},
    finding_samples=tuple(sample_findings),
    finding_totals=ValidationFindingTotals(
        findings=505973,
        errors=293375,
        warnings=212598,
    ),
    sheets=(ValidationSheet("Issue Counts", tuple(issue_rows)),),
)
```

If a validator already has all findings in memory but wants explicit totals, use
`ValidationFindingTotals.from_findings(findings)`.

Use `findings_export_limit=N` when a validator has full `findings` but only wants the generic
JSON/XLSX artifacts to include the first `N` rows. `findings.json` always uses this shape:

```json
{
  "total_findings": 505973,
  "errors": 293375,
  "warnings": 212598,
  "info": 0,
  "exported_findings": 1000,
  "truncated": true,
  "findings": []
}
```

`history.json` always uses this shape:

```json
{
  "schema_version": 1,
  "validator": "style-readiness",
  "run_id": "20260617T043001Z-style-readiness-e207690a",
  "started_at": "2026-06-17T04:30:01Z",
  "finished_at": "2026-06-17T04:30:07Z",
  "metrics": [
    {
      "scope": "brand",
      "brand": "CRAFT",
      "metric": "Style Completion %",
      "value": 42.4,
      "unit": "percent",
      "numerator": 120,
      "denominator": 283,
      "dimensions": {}
    }
  ]
}
```

For large or business-specific reports, prefer purpose-built XLSX/JSON files written under
`ctx.artifact_dir`. Keep generic findings focused on shared CLI output, machine-readable samples,
and lightweight diagnostics.

## Advanced Reports

Small validators can rely entirely on generic `findings` and `ValidationSheet` output. Larger
private validators often need a richer workbook, per-brand files, or other artifacts. In that case,
write the private artifacts yourself and return only the generic summary and finding samples the CLI
needs:

```python
from centric_api.validation import (
    ValidationDefinition,
    ValidationFinding,
    ValidationFindingTotals,
    ValidationResult,
)


class ReadinessValidator:
    definition = ValidationDefinition(
        name="readiness-check",
        title="Readiness Check",
        required_endpoints=("styles", "suppliers"),
    )

    def run(self, ctx):
        results = project_and_validate(ctx.records("styles"))
        report_workbook = build_readiness_workbook(results)
        write_brand_workbooks(ctx.artifact_dir / "brands", results)

        sample_findings = tuple(
            ValidationFinding(
                severity=row.severity,
                code=row.code,
                message=row.message,
                endpoint=row.endpoint,
                record_id=row.record_id,
                style_id=row.style_id,
                style_name=row.style_name,
                brand=row.brand,
                season=row.season,
            )
            for row in results.findings[:1000]
        )

        return ValidationResult(
            summary={
                "styles_checked": results.styles_checked,
                "ready_styles": results.ready_styles,
                "brand_workbooks": results.brand_count,
            },
            finding_samples=sample_findings,
            finding_totals=ValidationFindingTotals(
                findings=results.finding_count,
                errors=results.error_count,
                warnings=results.warning_count,
            ),
            report_workbook=report_workbook,
        )
```

This pattern keeps the default CLI and JSON artifacts useful without forcing the validator to
materialize every row as a generic `ValidationFinding`. `report_workbook` must be XLSX bytes; extra
files can be written anywhere below `ctx.artifact_dir`.

## History

Refresh validation history artifacts from first-class `history.json` files:

```bash
centric-api validate history
centric-api validate history --group day
centric-api validate history --group week
centric-api validate history --group month
centric-api validate history --validator style-readiness
```

The default grouping is `week`. If multiple runs land in the same bucket for the same
validator/metric/scope/brand, the latest run in that bucket wins. The command writes:

```text
CENTRIC_API_HOME/validation/history/
  history.html
  history.xlsx
  history.json
```

The HTML file is a self-contained graph and latest-value table. The XLSX file has grouped history,
latest values, and source runs. The JSON file is the canonical aggregated data for other tools.
Only run artifacts that contain schema version `1` `history.json` files participate; old runs
without history files are ignored by design.

## Context Helpers

The validation context provides:

- `ctx.records(endpoint)`: cached endpoint payloads, loaded lazily and cached.
- `ctx.records_any("bom_lines", "bomrows")`: first cached endpoint from aliases.
- `ctx.index_by_id(endpoint)`: endpoint payloads keyed by `id`.
- `ctx.index_by_id_any(...)`: first cached endpoint from aliases, keyed by `id`.
- `ctx.refs(value)`: normalized reference strings from scalars, arrays, or nested objects.
- `ctx.clean_ref(value)`: one normalized reference or `None`.
- `ctx.record_name(record)`: best available human label.
- `ctx.value_at(payload, "a.b.0.c")`: small JSON path helper.
- `ctx.units`: loaded unit registry.
- `ctx.artifact_dir`: current run artifact folder for optional private extra outputs.

Declare broad cache requirements in `ValidationDefinition.required_endpoints`. The runner checks
those endpoints before calling `run(ctx)`.

## Boundary

Keep business-specific reconstruction, validation rules, report slicing, and customer-specific
Excel output in private validator modules. The shared repo should stay limited to:

- validator discovery
- readonly cache access helpers
- generic JSON/XLSX artifact writing
- CLI rendering
- tests for the extension contract
