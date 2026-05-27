# Load Jobs

`centric-api load` validates spreadsheet rows and can send them to Centric as API requests. Bundled
jobs include `material-create`, which creates materials from an Excel workbook, and
`material-composition-create`, which posts parsed technical compositions onto existing materials.

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

## Path Templates And Array Bodies

Load paths can include `{column_key}` placeholders. Placeholders are replaced with the parsed row
value before the request is sent:

```yaml
path: /v2/materials/{material}/technical_compositions
```

`body` can be either an object mapping API fields to column keys or a single column key. A single
column key makes that column value the root JSON body, which is how
`material-composition-create` sends an array payload.

## Composition Lists

`composition_list` parses a natural-language composition string, validates that it totals 100%, and
resolves each composition name through cached `compositions`.

Accepted examples include:

```text
95%cotton;  5% polyester   .
50% Polyester, 50% Cotton
Polyester 50%, Cotton 50%
95 cotton 5 polyester
95 % cotton / 5 % elastane
```

The request body becomes:

```json
[
  {"percentage": 95, "composition": "C..."},
  {"percentage": 5, "composition": "C..."}
]
```

Composition parsing is case-insensitive for reference matching and tolerant of extra spacing,
optional `%`, commas, semicolons, slashes, plus signs, newlines, and trailing periods. Validation
fails before any API request when the total is not 100, a percentage is missing or invalid, a
composition cannot be found, or a composition name is ambiguous in the cache.

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
[load] artifacts: /Users/max/runtime/centric-api/load/runs/... requests=500
```

Real runs also print one send line per API request:

```text
[load] send: 1/500 status=201 row=2 elapsed=0.4s
```
