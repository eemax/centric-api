# View Exports

View exports turn cached Centric endpoint records into flat spreadsheet tables. They are local and
read-only: `centric-api view export` reads SQLite `endpoint_records` and writes an `.xlsx` or `.csv`
file without calling the Centric API.

Use views for business-facing tables such as BOM line exports, style/colorway rollups, supplier
worksheets, and QA extracts. Use `bundle` when you need to package downloaded document files.

## Commands

```bash
uv run centric-api view list
uv run centric-api view show style-colorways-demo
uv run centric-api view export style-colorways-demo
uv run centric-api view export style-colorways-demo --format csv
uv run centric-api view export style-colorways-demo --output ~/Desktop/style-colorways.xlsx
```

Config resolves from private `CENTRIC_API_HOME/views.yml` first, then `config/views.yml`, unless
`--view-config PATH` is passed. Default exports are written under `CENTRIC_API_HOME/exports`.
Filters are part of the schema, not command-line flags, so production exports stay repeatable.

## Mental Model

A view has one root and zero or more joins. The root plus any `many_expand` joins define row grain.
All other joined arrays must use `many_concat`, otherwise the view is invalid.

For example, if `styles` is the root and `colorways` is a `many_expand` join, each output row is one
style/colorway pair. Joined season, collection, supplier, or factory values can be repeated onto
each expanded row.

This is valid:

```yaml
root:
  endpoint: styles
  as: style
joins:
  - as: colorway
    endpoint: colorways
    from: style.id
    to: style
    relationship: many_expand
    missing: drop
  - as: season
    endpoint: seasons
    from: style.parent_season
    to: id
    relationship: one
```

This is also valid because it has one linear expansion chain:

```yaml
root:
  endpoint: styles
  as: style
joins:
  - as: colorway
    endpoint: colorways
    from: style.id
    to: style
    relationship: many_expand
  - as: bom_line
    endpoint: bom_lines
    from: colorway.id
    to: colorway
    relationship: many_expand
```

This is invalid because it expands two unrelated arrays from the same root and would create fake
colorway/document combinations:

```yaml
root:
  endpoint: styles
  as: style
joins:
  - as: colorway
    endpoint: colorways
    from: style.id
    to: style
    relationship: many_expand
  - as: document
    endpoint: documents
    from: style.documents
    to: id
    relationship: many_expand
```

Make one of those arrays `many_concat` instead.

Filters can be applied at two levels:

- Join filters reduce the records attached by that join.
- View filters reduce exported rows as soon as their referenced aliases are available.

This keeps output tables clean without asking the user to export everything and clean it in Excel.

## Schema Reference

```yaml
version: 1
output_dir: exports

options:
  missing: blank
  many_separator: ", "
  freeze_header: true
  autofilter: true
  autosize: true

views:
  - name: style-colorways-demo
    title: Style Colorways Demo
    root:
      endpoint: styles
      as: style
    joins:
      - as: colorway
        endpoint: colorways
        from: style.id
        to: style
        relationship: many_expand
        missing: drop
      - as: collection
        endpoint: collections
        from: style.collection
        to: id
        relationship: one
    filters:
      - path: style.active
        equals: true
    columns:
      - header: Style ID
        path: style.id
        type: text
        width: 24
      - header: Style
        path: style.node_name
        type: text
      - header: Collection
        path: collection.node_name
        type: text
      - header: Colorway
        path: colorway.node_name
        type: text
```

Top-level fields:

- `version`: schema version. Must be `1`.
- `output_dir`: export output directory. Relative paths resolve under `CENTRIC_API_HOME`.
- `options`: defaults inherited by every view.
- `views`: configured view definitions.

View fields:

- `name`: command name used by `view show` and `view export`.
- `title`: human-readable title and default Excel sheet title.
- `root.endpoint`: cached endpoint used as the starting table.
- `root.as`: alias for the root record in joins and column paths.
- `joins`: optional ordered joins. A join can reference aliases created by earlier joins.
- `filters`: optional final row filters. Filters can reference root or joined aliases.
- `columns`: ordered output columns.
- `options`: per-view overrides.

## Aliases And Paths

Every record source has an alias. Column paths and join `from` paths always start with an alias:

```yaml
path: style.node_name
from: colorway.style
```

Aliases make it possible to join the same endpoint more than once:

```yaml
joins:
  - as: primary_supplier
    endpoint: suppliers
    from: style.primary_supplier
    to: id
    relationship: one
  - as: nominated_supplier
    endpoint: suppliers
    from: style.nominated_supplier
    to: id
    relationship: one
```

Paths are simple dot paths through JSON objects. Numeric list indexes are supported, such as
`measurements.0.value`, but view schemas should prefer stable object fields over positional list
indexes.

## Joins

Join fields:

- `as`: alias created by the join.
- `endpoint`: cached endpoint to read.
- `from`: path on an existing alias. Values are used as join keys.
- `to`: path on the joined endpoint. Records whose `to` value matches `from` are joined.
- `relationship`: `one`, `many_concat`, or `many_expand`.
- `missing`: optional `blank`, `drop`, or `error`.
- `separator`: optional string for `many_concat` columns from this alias.
- `filters`: optional filters applied to candidate joined records before they are attached.

Relationships:

- `one`: match zero or one record. If multiple records match, the first deterministic match is used
  and a warning is reported.
- `many_concat`: keep row grain unchanged. Column values from this alias are joined into one cell.
- `many_expand`: expand rows. Multiple matching records produce multiple output rows.

The implementation intentionally allows only one linear `many_expand` chain. After one
`many_expand` join appears, the next `many_expand` must join from the active expansion alias, or from
a `one` join derived from that active expansion alias. This keeps the view from accidentally
producing cartesian products while still allowing parent/detail lookups inside the row-grain chain.

Missing behavior:

- `blank`: keep the row and leave joined columns blank.
- `drop`: omit the row for that missing join.
- `error`: fail the export.

Default missing behavior is `blank`.

## Filters

Filters are AND-only. A row or joined record must satisfy every filter in the relevant list.

Supported operators:

- `equals`
- `in`
- `contains`
- `matches`
- `exists`
- `gt`
- `gte`
- `lt`
- `lte`

View filters run as soon as their referenced alias is available. Root-only filters run before joins;
filters on joined aliases run after that join has been applied:

```yaml
filters:
  - path: style.active
    equals: true
  - path: season.node_name
    in:
      - SS26
      - FW26
  - path: bom_line.quantity
    gt: 0
```

Join filters trim candidate joined records:

```yaml
joins:
  - as: document
    endpoint: documents
    from: style.documents
    to: id
    relationship: many_concat
    filters:
      - path: document.document_type
        in:
          - Tech Pack
          - Specification
      - path: document.active
        equals: true
```

For list-valued paths and `many_concat` aliases, a filter passes when any value matches. Join filters
on `many_concat` trim the values that appear in the final cell; view filters on `many_concat` decide
whether the final row is kept.

## Columns And Formatting

Column fields:

- `header`: spreadsheet header.
- `path`: alias path to read.
- `type`: `text`, `number`, `integer`, `boolean`, `date`, or `datetime`.
- `width`: optional Excel column width.
- `number_format`: optional Excel number/date format.

Example:

```yaml
columns:
  - header: Modified
    path: bom_line._modified_at
    type: datetime
    width: 22

  - header: Unit Cost
    path: bom_line.unit_cost
    type: number
    number_format: "0.00"

  - header: Active
    path: style.active
    type: boolean
```

CSV output writes plain text values. XLSX output applies header styling, optional frozen headers,
autofilter, autosized columns, and type-aware cell values.

## Authoring Checklist

Choose the root as the lowest denominator of the table. If every row should be a BOM line, root the
view at `bom_lines`. If every row should be a style-colorway pair, root at `styles` and use one
`many_expand` to `colorways`.

Prefer `one` for parent/reference data such as style season, supplier, factory, category, or user.
Use `many_concat` for supplementary arrays that should be repeated in one cell. Use `many_expand`
only when the joined records should become rows.

Put stable production filters in the view schema. Use join filters when you want to trim a joined
array before displaying it; use view filters when you want to drop rows.

When a view feels hard to model, check for two unrelated arrays. One of them probably needs to be
`many_concat`, or the view should be split into two exports.

Fetch every endpoint used by the view before exporting. Missing endpoints simply produce no joined
records; `doctor` may grow view-specific checks later, but the export itself is intentionally
local-cache based.
