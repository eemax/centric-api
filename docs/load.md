# Load Jobs

`centric-api load` validates spreadsheet rows and can send them to Centric as API requests. The
first bundled job is `material-create`, which creates materials from an Excel workbook.

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

The default bundled job:

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
      node_name:
        header: Material Name
        headers: [Material, Name]
        type: text
        required: true
      material_type:
        header: Material Type
        headers: [Type]
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
      node_name: node_name
      product_type: material_type
      description: description
```

## Header Matching

Each column has one canonical `header` and optional `headers` aliases. Matching is trimmed and
case-insensitive.

If more than one accepted header for the same column exists in a workbook, validation fails as
ambiguous. For example, if `Material Name` and `Name` both exist, the loader refuses to guess.

## Reference Resolution

`ref` columns resolve user-facing workbook values through the local SQLite cache:

```yaml
material_type:
  header: Material Type
  type: ref
  required: true
  resolve:
    endpoint: material_types
    match: node_name
    output: id
```

The workbook value is matched exactly after trimming and case-folding. No match is a row error.
Multiple matches are a row error. The resolved `output` value is used in the request body. For the
bundled material job, the workbook column is `Material Type` and the request body field is
`product_type`.

Resolvers can include simple exact-match `filters` applied to cached reference records before
matching the workbook value. The bundled material job uses `available: true` so historical material
types with duplicate display names do not make common workbook values ambiguous.

Fetch reference endpoints before checking or running a load job.

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
