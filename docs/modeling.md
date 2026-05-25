# Modeling

`centric-api model` runs calculated local data models from Python modules in
`CENTRIC_API_HOME/models`. Models are intentionally code-first: joins, conditional logic, validation,
and business formulas live in normal Python instead of a large YAML expression language.

The model layer sits between fetched records and view exports:

```text
fetch       -> cache Centric records in SQLite
model run   -> refresh calculated local tables
next step   -> export or report from model output tables
```

## CLI

```bash
centric-api model list
centric-api model show my-model
centric-api model check my-model
centric-api model run my-model
```

Options:

- `--models-dir PATH`: load private model modules from a specific directory.
- `--units-config PATH`: use an explicit unit registry.
- `--db PATH`: use a non-default SQLite cache for `check` or `run`.
- `--json`: emit machine-readable output.

## Model Modules

Each model file exposes `MODEL` or `get_model()` and implements a small interface:

```python
from centric_api.modeling import ModelColumn, ModelDefinition, ModelOutput


class MyModel:
    definition = ModelDefinition(
        name="my-model",
        title="My Model",
        output_table="model_my_model",
        required_endpoints=("styles",),
    )

    def check(self, ctx):
        ...

    def run(self, ctx):
        return ModelOutput(
            columns=(ModelColumn("style_id", "text"),),
            rows=({"style_id": "S1"},),
        )


MODEL = MyModel()
```

`check` should validate inputs and report issues through `ctx.error(...)` or `ctx.warning(...)`.
`run` should calculate rows and return a `ModelOutput`.

## Context

The model context provides reusable engine services:

- `ctx.records(endpoint)`: cached endpoint payloads.
- `ctx.records_any("bom_lines", "bomrows")`: first cached endpoint from aliases.
- `ctx.index_by_id(endpoint)`: endpoint payloads keyed by `id`.
- `ctx.index_by_id_any(...)`: first cached endpoint from aliases, keyed by `id`.
- `ctx.units`: loaded unit registry.
- `ctx.error(...)` and `ctx.warning(...)`: structured run issues.

## Output Tables

Successful `model run` replaces one stable SQLite output table, such as `model_my_model`. It does
not create a new output table per run.

Runs are recorded in:

- `model_runs`
- `model_run_issues`

`model_runs.metrics_json` stores optional model-specific counters such as `ok_rows`, `error_rows`,
or skipped-row totals. If a model run has fatal errors, the previous output table is left untouched.
Row-level data problems should usually be written as diagnostic output rows and reported as warning
issues, so valid rows can still be published with `status=attention`.

Model output tables can be exported through the normal view system by using `table` instead of
`endpoint` in a private view config:

```yaml
views:
  - name: my-model-export
    root:
      table: model_my_model
      as: model
    joins:
      - as: style
        endpoint: styles
        from: model.style_id
        to: id
        relationship: one
    columns:
      - header: Style
        path: style.node_name
      - header: Value
        path: model.value
        type: number
```

## Units

Models should use the first-class unit registry documented in [Units](units.md). Private models can
call `ctx.units.normalize(...)`, `ctx.units.convert(...)`, and `ctx.units.basis(...)`.

Unknown units, incompatible conversions, missing references, and missing business inputs should be
reported during `model check` or `model run` with enough endpoint/record context to fix the cached
data.

## Authoring Checklist

Use this checklist when adding a private model. The goal is that every model is predictable to run,
easy to debug, and easy to export.

- Keep private business logic in `CENTRIC_API_HOME/models`; keep the public repo limited to shared
  runtime helpers.
- Give every model a stable `name`, human `title`, and SQLite-safe `output_table`, usually prefixed
  with `model_`.
- Put broad endpoint requirements in `required_endpoints`, then use `ctx.records_any(...)` or
  `ctx.index_by_id_any(...)` inside the model when endpoint names have known variants.
- Use `check(ctx)` for fast preflight validation. It should inspect cache shape and required lookup
  data, but should not replace output tables.
- Use `run(ctx)` for the full calculation and return a `ModelOutput`.
- Treat `ctx.error(...)` as fatal. If any errors are reported, the run status is `failed` and the
  previous output table is left untouched.
- Prefer `ctx.warning(...)` for row-level data problems when valid rows can still be published. Pair
  those warnings with diagnostic output rows so the exported table can show what failed.
- Include a `row_status` column when the model can publish partial results. Use values such as `ok`
  and `error`, and filter exports to `row_status == ok` when users want the clean result.
- Include diagnostic columns such as `issue_code`, `issue_message`, and the relevant source record
  ids. Keep normal successful rows at the model's actual grain instead of repeating low-level audit
  details unnecessarily.
- Make the output grain explicit in the columns. If a row may represent a style, style-colorway,
  supplier, or another scope, include clear scope columns instead of relying on blank ids.
- When a row aggregates across a group, include the resolved ids or counts that prove what was
  covered. For example, a style-level row can include `resolved_colorway_count` and
  `resolved_colorway_ids`.
- Validate references that define whether a row is truly resolved. A row should not be `ok` if a
  required joined record is missing from the cache.
- Use `ctx.units` for unit normalization and conversion. Do not reimplement unit math in each model.
- Store model-specific counters in `ModelOutput.metrics`, using stable names such as `ok_rows`,
  `error_rows`, `skipped_rows`, or domain-specific input counters.
- Keep metrics numeric or simple JSON-compatible values so CLI output and `model_runs.metrics_json`
  stay useful.
- Make output column names SQLite-safe and stable. View configs and downstream exports will depend
  on them.
- Use `ModelColumn(..., "json")` for structured lists or objects that should stay machine-readable.
- Make private view configs consume model output tables with `table: model_name`, then join cached
  endpoints for display labels and filters.
- Run `centric-api model check MODEL` before the first full run, then `centric-api model run MODEL`,
  then `centric-api view check VIEW` for any export that depends on the model table.
- A clean model-backed view should ideally have no missing joins. If a view reports missing refs,
  decide whether the model should downgrade those rows to diagnostics instead of `ok`.
