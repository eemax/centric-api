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
centric-api model show style-bom-consumption
centric-api model check style-bom-consumption
centric-api model run style-bom-consumption
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

Successful `model run` replaces one stable SQLite output table, such as
`model_style_bom_consumption`. It does not create a new output table per run.

Runs are recorded in:

- `model_runs`
- `model_run_issues`

If a model run has errors, the previous output table is left untouched.

## Units

Models should use the first-class unit registry documented in [Units](units.md). Private models can
call `ctx.units.normalize(...)`, `ctx.units.convert(...)`, and `ctx.units.basis(...)`.

Unknown units, incompatible conversions, missing references, and missing business inputs should be
reported during `model check` or `model run` with enough endpoint/record context to fix the cached
data.
