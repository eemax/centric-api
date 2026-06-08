# Load Jobs

`centric-api load` validates spreadsheet rows and can send them to Centric as API requests. Bundled
jobs include `material-create`, which creates materials from an Excel workbook,
`material-composition-create`, which posts parsed technical compositions onto existing materials,
and `style-bom-load`, which creates a BOM header, owned sections, and material lines from one
workbook.

Load jobs are intentionally schema-driven but small: the schema maps workbook headers to typed
fields, resolves simple cached references, and maps fields into a request body.

## Commands

```bash
uv run centric-api load list
uv run centric-api load show material-create
uv run centric-api load check material-create materials.xlsx
uv run centric-api load check material-create materials.xlsx --sheet Materials
uv run centric-api load run material-create materials.xlsx --dry-run
uv run centric-api load run material-create materials.xlsx --yes
uv run centric-api load retry material-create /path/to/review.xlsx --dry-run
uv run centric-api load retry material-create /path/to/review.xlsx --yes
uv run centric-api load check material-composition-create material-compositions.xlsx
uv run centric-api load run style-bom-load style-bom-lines.xlsx --dry-run
```

`--sheet` is optional. If omitted, the first worksheet is used. `check` validates the workbook and
reference resolution without API calls. `run --dry-run` writes planned request artifacts without API
calls. A real run requires `--yes`.

Human `check`, `run`, and `retry` commands write progress lines to stderr while the final summary
stays on stdout. `--json` suppresses progress so stdout remains machine-readable. Use `--quiet` to
suppress human progress while keeping the normal human summary.

## Config

Config resolves from `config/load.yml` plus private `CENTRIC_API_HOME/load.yml` when present, unless
`--load-config PATH` is passed. Private jobs with the same name replace bundled jobs; private jobs
with new names are added.

`load list` and `load show` include each job's source:

- `bundled`: loaded from the repo's `config/load.yml`.
- `private`: loaded from `CENTRIC_API_HOME/load.yml`.
- `explicit`: loaded from a `--load-config PATH` file.

When a private job has the same `name` as a bundled job, only the private job is selected and shown.
Use `load show JOB` to confirm the exact config path before running a load.

The default bundled material job:

```yaml
version: 1

jobs:
  - name: material-create
    title: Material Create
    method: POST
    path: /v2/materials
    input:
      header_row: 1
    columns:
      code:
        header: Code
        headers: [Material Code]
        type: text
        required: true
      product_type:
        header: Product Type
        headers: [Material Type, Type]
        type: ref
        required: true
        resolve:
          endpoint: material_types
          match: node_name
          output: id
          filters:
            available: true
      description:
        header: Description
        headers: [Desc]
        type: text
    body:
      code: code
      product_type: product_type
      description: description
```

The bundled material composition job accepts either a cached material ID or a material code in the
`Material` column, then posts a root JSON array to
`/v2/materials/{material}/technical_compositions`:

```yaml
- name: material-composition-create
  title: Material Composition Create
  method: POST
  path: /v2/materials/{material}/technical_compositions
  columns:
    material:
      header: Material
      headers: [Material ID, Material Id, Material Code, Code]
      type: ref_or_id
      required: true
      resolve:
        endpoint: materials
        match: code
        output: id
    compositions:
      header: Composition
      headers: [Technical Composition, Material Composition, Fiber Content, Content]
      type: composition_list
      required: true
      resolve:
        endpoint: compositions
        match: node_name
        output: id
        filters:
          active: true
  body: compositions
```

The bundled style BOM load workflow resolves style/BOM header fields, then chains the API calls
needed to create sections and material lines:

```yaml
- name: style-bom-load
  title: Style BOM Load
  workflow: style_bom
  method: POST
  path: /v2/styles/{style}/data_sheets/apparel_boms
```

Expected workbook columns:

```text
Season, Style, BOM Name, Description, Subtype, Section, PM ID, Quantity, Material Code
```

This order is recommended for readable workbooks, but the loader matches columns by header name, so
the actual Excel column order does not matter.

Rows are grouped by resolved style, BOM name, description, and subtype. For each group the workflow:

- posts the BOM header to `/v2/styles/{style}/data_sheets/apparel_boms`.
- reads `latest_revision` from the header response.
- validates every `Section` exactly and case-sensitively against cached `bom_sections.node_name`
  records where `active` is `true` and `ad_hoc` is `false`.
- posts each unique section once to
  `/v2/apparel_bom_revisions/{revision}/owned_sections/bom_section_definition`.
- resolves `Material Code` through cached `materials.code`.
- posts each line to `/v2/apparel_bom_revisions/{revision}/items/part_materials` with
  `ds_section`, `pm_id`, `qty_default`, and `actual`.

`PM ID`, `Quantity`, and `Material Code` come from the workbook. The section creation response id is
used as `ds_section`, and the material cache id is used as `actual`.

## Header Matching

Each column has one canonical `header` and optional `headers` aliases. Matching is trimmed and
case-insensitive.

If more than one accepted header for the same column exists in a workbook, validation fails as
ambiguous. For example, if `Product Type` and `Type` both exist, the loader refuses to guess.

## Reference Resolution

`ref` columns resolve user-facing workbook values through the local SQLite cache:

```yaml
product_type:
  header: Product Type
  headers: [Material Type, Type]
  type: ref
  required: true
  resolve:
    endpoint: material_types
    match: node_name
    output: id
```

The workbook value is matched exactly after trimming and case-folding. No match is a row error.
Multiple matches are a row error. The resolved `output` value is used in the request body. For the
bundled material job, the workbook column can be `Product Type` or `Material Type`, and the request
body field is `product_type`.

Resolvers can include simple exact-match `filters` applied to cached reference records before
matching the workbook value. The bundled material job uses `available: true` so historical material
types with duplicate display names do not make common workbook values ambiguous.

Fetch reference endpoints before checking or running a load job.

`ref_or_id` columns are useful when a workbook may contain either a Centric ID or a user-facing
lookup value. The loader first tries the workbook value against the cached reference `output` field,
then falls back to the configured `match` field. For the bundled material composition job this means
`Material` can contain either `materials.id` or `materials.code`. Duplicate code matches fail as row
validation errors.

`scoped_ref` columns resolve a workbook value through one endpoint, then require the matched record
to point at a scoped record whose value matches another workbook column:

```yaml
style:
  header: Style
  type: scoped_ref
  required: true
  resolve:
    endpoint: styles
    match: node_name
    output: id
    scope:
      column: season
      endpoint: seasons
      via: parent_season
      match: node_name
```

In this example, `Style` first matches `styles.node_name`. Candidate styles must then have
`styles.parent_season` pointing at a cached `seasons` record whose `node_name` matches the workbook
`Season` column. No match, missing scoped records, and multiple scoped matches are row validation
errors.

## Value Sets

`value_set` columns validate and canonicalize text values from private XLSX truth lists. Store value
sets under `CENTRIC_API_HOME/load/value-sets/{name}.xlsx`. The first sheet's column A contains the
allowed values with no header:

```yaml
fabric_type:
  header: Fabric Type
  type: text
  required: true
  value_set:
    name: materials.fabric_type
```

The example above reads `CENTRIC_API_HOME/load/value-sets/materials.fabric_type.xlsx`. The workbook
value is matched after trimming, Unicode normalization, case-folding, whitespace cleanup, separator
cleanup, and conservative singular/plural normalization. The request body uses the exact canonical
value from the value set workbook. If two allowed values normalize to the same lookup key, the value
set fails before any rows are processed.

## Path Templates And Array Bodies

Load paths can include `{column_key}` placeholders. Placeholders are replaced with the parsed row
value before the request is sent:

```yaml
path: /v2/materials/{material}/technical_compositions
```

`body` can be either an object mapping API fields to column keys or a single column key. A single
column key makes that column value the root JSON body, which is how
`material-composition-create` sends an array payload.

Jobs with `workflow: style_bom` are dedicated chained workflows. They still use the configured
columns and cached reference resolution, but their requests are generated by Python workflow code so
responses from earlier calls can feed later calls.

## Composition Lists

`composition_list` parses a natural-language composition string, validates that it totals 100%, and
resolves each composition name through cached `compositions`.

Accepted examples include:

```text
95%cotton;  5% polyester   .
50% Polyester, 50% Cotton
Polyester 50%, Cotton 50%
95 cotton 5 polyester
polyester-recycled 95 5 elastane
95 polyester-recycled; elastane 5
95 % cotton / 5 % elastane
```

The request body becomes:

```json
[
  {"percentage": 95, "composition": "C..."},
  {"percentage": 5, "composition": "C..."}
]
```

Composition parsing treats every numeric token as a percentage, so percentages can appear before or
after composition names. Reference matching first tries the exact trimmed/case-folded name, then a
strict canonical fallback where punctuation, separators, and word order do not matter. For example,
`polyester-recycled`, `recycled polyester`, and `Polyester - Recycled` can resolve to the same
cached composition record. Words still need to be spelled out; shorthand such as `poly` is not fuzzy
matched. Validation fails before any API request when the total is not 100, a percentage is missing
or invalid, a composition cannot be found, or a composition name is ambiguous in the cache.

## Safety

- `check` makes no API calls.
- `run --dry-run` makes no API calls and writes `requests.jsonl` plus `summary.json`.
- real runs require `--yes`.
- rows with validation or reference errors are marked `validation_error` and are not sent.
- optional blank fields are omitted from the request body.
- run artifacts are written under `CENTRIC_API_HOME/load/runs/{run_id}`.
- real runs with API responses, and any run with validation errors, write a review workbook at
  `CENTRIC_API_HOME/load/runs/{run_id}/review.xlsx`.

## Review Workbooks

Review workbooks are copies of the input workbook with load result columns appended at the far right.
The source workbook is never modified.

```text
_cent_load_run_id
_cent_load_status
_cent_load_status_code
_cent_load_message
_cent_load_request_path
_cent_load_response_id
_cent_load_processed_at
```

Statuses are intentionally small:

- `success`: the row was sent and Centric returned a status below 400.
- `failed`: the row was attempted, but Centric returned 400 or higher, or the request raised.
- `validation_error`: the row was not sent because local validation or reference resolution failed.

Blank review status means the row was not processed in that run. For example, a clean dry-run has no
API outcome to mark.

Retry uses the review workbook as the editable source:

```bash
uv run centric-api load retry material-create review.xlsx --dry-run
uv run centric-api load retry material-create review.xlsx --yes
```

By default, retry processes rows where `_cent_load_status` is `failed` or `validation_error`.
Override that with `--statuses failed` or `--statuses failed,validation_error`. Retry revalidates the
selected rows before sending and writes a fresh run directory with fresh artifacts.

## Progress

Progress lines are phase-oriented for checks and dry runs:

```text
[load] planning: job=material-create mode=dry-run workbook=materials.xlsx sheet=Materials
[load] headers: matched=4/4 required=3/3 aliases=1 issues=0
[load] refs: material_types matched=42 values=42 filter=available:true
[load] validate: scanned=500 valid=500 errors=0
[load] artifacts: $CENTRIC_API_HOME/load/runs/... requests=500
```

Real runs also print one send line per API request:

```text
[load] send: 1/500 status=201 row=2 elapsed=0.4s
```
