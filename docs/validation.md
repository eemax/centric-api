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

For large or business-specific reports, prefer purpose-built XLSX/JSON files written under
`ctx.artifact_dir`. Keep generic findings focused on shared CLI output, machine-readable samples,
and lightweight diagnostics.

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
