# Modeling Spec

This is a future implementation spec for calculated, business-shaped local datasets. The feature is
not implemented yet.

The model layer should sit between fetched records and view exports:

```text
fetch       -> cache Centric records in SQLite
model run   -> build calculated local tables from cached records
view export -> export endpoint or model output tables
```

## CLI Shape

```bash
centric-api model list
centric-api model show material-consumption
centric-api model check material-consumption
centric-api model run material-consumption
centric-api model run --all
```

## Config

Models should resolve from `config/models.yml`, then private `CENTRIC_API_HOME/models.yml`, with an
override flag such as `--model-config`.

```yaml
version: 1

models:
  - name: material-consumption
    title: Material Consumption
    output: model_material_consumption

    grain:
      - style_id
      - bom_id
      - material_id

    root:
      endpoint: styles
      as: style

    joins:
      - as: bom
        endpoint: boms
        from: style.id
        to: style
        relationship: many
        filters:
          - path: bom.active
            equals: true

      - as: line
        endpoint: bom_lines
        from: bom.id
        to: bom
        relationship: many

      - as: material
        endpoint: materials
        from: line.material
        to: id
        relationship: one

    filters:
      - path: style.active
        equals: true

    columns:
      - name: style_id
        expression: style.id
        type: text

      - name: bom_id
        expression: bom.id
        type: text

      - name: material_id
        expression: material.id
        type: text

      - name: line_quantity
        expression: to_number(line.quantity)
        type: number

      - name: material_weight_kg
        expression: unit_convert(material.weight, material.weight_uom, "kg")
        type: number

      - name: total_weight_kg
        expression: line_quantity * material_weight_kg
        type: number

    aggregate:
      group_by:
        - style_id
        - bom_id
        - material_id
      measures:
        - name: total_quantity
          op: sum
          expression: line_quantity
        - name: total_weight_kg
          op: sum
          expression: total_weight_kg
```

## Rules

Every model must declare grain. Grain is the contract that prevents accidental row multiplication
and unclear rollups.

Relationships must be explicit:

- `one`: enrich the current row
- `many`: expand rows

Models should persist output to SQLite tables, such as `model_material_consumption`, and record run
metadata in model run tables.

## Units

Models should use the first-class unit registry documented in [Units](units.md). Unit conversion
must happen before aggregation whenever source rows can contain mixed units:

```yaml
expression: unit_convert(line.quantity, line.uom, "kg")
```

Unknown units and incompatible conversions should fail `model check` or `model run` loudly with
model name, path, and sample record context.

## Initial Scope

The first model implementation should stay narrow:

- list/show/check/run commands
- cached endpoint inputs only
- explicit joins and filters
- basic expression engine with path references, arithmetic, `to_number`, `coalesce`, and
  `unit_convert`
- `group_by` plus `sum`, `count`, `min`, and `max`
- replace output tables per successful run

View export integration can come after model output tables exist.
