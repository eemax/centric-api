# Configuration

Runtime state defaults to `~/.centric-api`. Set `CENTRIC_API_HOME` to move private config, logs,
locks, raw evidence, downloads, bundles, and the default SQLite database.

## Resolution Rules

| Purpose | Default | Private default | Override |
| --- | --- | --- | --- |
| Runtime home | `~/.centric-api` | n/a | `CENTRIC_API_HOME` |
| Fetch config | `config/fetcher.yml` | n/a | `--fetch-config` |
| Credentials | environment | `CENTRIC_API_HOME/local.env` | `--env-file` |
| SQLite DB | `CENTRIC_API_HOME/centric.db` | n/a | `--db` |
| Delta state | `CENTRIC_API_HOME/delta.yml` | n/a | `--delta-state-file` |
| Endpoint schema | `config/endpoint-schema.yml` plus private overlay when present | `CENTRIC_API_HOME/endpoint-schema.yml` | `--schema` |
| Download config | `config/download.yml` | `CENTRIC_API_HOME/download.yml` | `--download-config` |
| Bundle config | `config/bundle.yml` | `CENTRIC_API_HOME/bundle.yml` | `--bundle-config` |
| View config | `config/views.yml` | `CENTRIC_API_HOME/views.yml` | `--view-config` |
| Load config | `config/load.yml` plus private overlay when present | `CENTRIC_API_HOME/load.yml` | `--load-config` |
| Units config | `config/units.yml` plus private overlay when present | `CENTRIC_API_HOME/units.yml` | `--units-config` |
| Model modules | n/a | `CENTRIC_API_HOME/models/*.py` | `--models-dir` |
| Validator modules | n/a | `CENTRIC_API_HOME/validators/*.py` | `--validators-dir` |

Relative runtime paths inside configs resolve under `CENTRIC_API_HOME`. Absolute paths and `~` are
respected.

## Credentials

Credentials can be environment variables or entries in `CENTRIC_API_HOME/local.env`:

```bash
CENTRIC_BASE_URL=https://centric.example.com
CENTRIC_USERNAME=user@example.com
CENTRIC_PASSWORD=secret
```

Fetch config intentionally rejects `base_url` and `auth`; keep secrets outside shared config files.

## Fetcher Config

`config/fetcher.yml` defines endpoint fetch behavior. Fetcher configs reject unknown keys so typos
fail before a fetch starts.

```yaml
timeout: 30
retry_max_attempts: 3
retry_base_seconds: 15
retry_max_seconds: 30
output_dir: raw
checkpoint_dir: checkpoints

endpoints:
  - name: styles
    api_version: v2
    path: styles
    query_params:
      active: true
    limit: 50
    count_spec:
      path: count/Style
      query_params:
        active: true
```

Endpoint entries require:

- `name`: local endpoint name used by `--endpoint`, database rows, and files.
- `api_version`: `v2` or `v3`.
- `path`: API path without leading/trailing slashes.
- `count_spec.path`: API count path.

Optional fields:

- `query_params`: sent with data requests.
- `count_spec.query_params`: sent with count requests.
- `limit`: page size, default `50`.

Delta, day, and month fetches force sorting by `_modified_at` and replace any existing
`_modified_at` filters with the runtime floor.

## Delta State

If `CENTRIC_API_HOME/delta.yml` does not exist, the first delta fetch starts with no floor. Successful
endpoint fetches advance `last_successful_fetch_*` only after the run manifest, SQLite ingest, and
changelog pipeline complete cleanly. A typical file looks like:

```yaml
overlap_minutes: 10
overlap_days: 0
endpoints:
  styles:
    last_successful_fetch_start: "2026-01-01T00:00:00Z"
    last_successful_fetch_end: "2026-01-01T00:01:00Z"
```

The overlap is subtracted from the previous successful endpoint start time to tolerate late writes
or clock skew.

## Endpoint Schema

Endpoint schema only describes tombstone rules. Identity is always `id`, and freshness is always
`_modified_at`. Endpoint schema files reject unknown keys and use version `1` when a version is
provided.

```yaml
version: 1

endpoints:
  styles:
    delete_when_any:
      - field: active
        equals: false
    delete_when_any_add:
      - field: state
        equals: Deleted
```

A matching payload deletes the current record, writes an endpoint tombstone, and creates changelog
removal events. `delete_when_any` replaces existing rules for that endpoint; `delete_when_any_add`
adds rules to the defaults or an earlier shared schema file.

## Download Config

Download configs use version `1` and reject unknown keys.

```yaml
version: 1
output_dir: downloads

jobs:
  - name: ss26-style-techpacks
    sources:
      - endpoint: styles
        filters:
          - path: active
            equals: true
          - path: season
            lookup:
              endpoint: seasons
              path: node_name
              in:
                - SS26
                - Summer 2026
    document_filters:
      - path: document_type
        in:
          - Tech Pack
    revision_filters:
      - path: file_name
        matches: "\\.pdf$"
```

Jobs require:

- `name`
- at least one `sources` entry

Sources require:

- `endpoint`
- optional `filters`

Each job must define at least one source.

Filter operators:

- `equals`
- `in`
- `contains`
- `matches`
- `exists`
- `lookup`

Each filter must define exactly one operator. `in` must be a non-empty array. `exists` must be a
boolean. `matches` is a regular expression string.

Filter scopes:

- Source filters apply to source records such as `styles`, `materials`, or `suppliers`.
- `document_filters` apply to cached `documents` records.
- `revision_filters` apply to cached `document_revisions` records.

Lookup filters compare a reference ID on the source record to a cached lookup endpoint. For example,
`path: season` with `lookup.endpoint: seasons` and `lookup.path: node_name` filters styles by the
referenced season name. Lookup filters are intentionally narrow: source-side arrays are not matched.

Every download job needs cached evidence for its source endpoints, `documents`, `document_revisions`,
and any lookup endpoints. `doctor` reports missing prerequisites.

## Bundle Config

Bundle configs use version `1` and reject unknown keys.

```yaml
version: 1
output_dir: bundles

bundles:
  - name: ss26-style-techpacks
    download_job: ss26-style-techpacks
    layout:
      source_label:
        default:
          fields:
            - node_name
        styles:
          fields:
            - style_code
            - node_name
          join: " - "
```

Bundles require:

- `name`
- `download_job`

Layout options:

- `layout.source_label.default`: fallback label rule.
- `layout.source_label.ENDPOINT`: endpoint-specific label rule.
- `fields`: non-empty list of source record fields to concatenate.
- `join`: string separator, default `" - "`.

Archive paths use the shape `files/{source_endpoint}/{source_label}/{filename}`. If the same
document is referenced by multiple source objects, the bundle includes one copy under each source
object folder.

## View Config

View configs use version `1` and reject unknown keys. They define flat spreadsheet views over cached
endpoint records or calculated model output tables:

```yaml
version: 1
output_dir: exports

views:
  - name: bom-lines
    title: BOM Lines
    root:
      endpoint: bom_lines
      as: bom_line
    joins:
      - as: style
        endpoint: styles
        from: bom_line.style
        to: id
        relationship: one
    filters:
      - path: style.active
        equals: true
    columns:
      - header: BOM Line ID
        path: bom_line.id
        type: text
      - header: Style
        path: style.node_name
        type: text
```

Use `docs/views.md` as the full schema reference. The core rule is that the root plus one linear
`many_expand` chain define row grain; other arrays should be `many_concat`. Filters can live on joins
or on the final view and can reference joined aliases. Use `endpoint` for cached Centric records and
`table` for SQLite model output tables.

## Load Config

Load configs use version `1` and reject unknown keys. They define spreadsheet-to-request jobs:

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
    body:
      code: code
      product_type: product_type
```

Use `docs/load.md` as the full schema reference. Bundled load jobs include `material-create`,
`material-composition-create`, and `style-bom-load`; all can be extended or replaced by private
`CENTRIC_API_HOME/load.yml`. Private load value sets live under
`CENTRIC_API_HOME/load/value-sets/{name}.xlsx`.

## Units Config

Units configs use version `1` and reject unknown keys. The default registry in `config/units.yml`
defines common metric, US customary, and trade units. A private `CENTRIC_API_HOME/units.yml` extends
the defaults with extra dimensions, units, or aliases; `--units-config PATH` loads one explicit
registry instead.

```yaml
version: 1

dimensions:
  mass:
    base: kg
    consumption:
      basis: direct_mass
      bom_quantity: mass
      material_value: ignored
      output_unit: kg
      requires: [bom_quantity, material_uom]
      formula: unit_convert(bom_quantity, material_uom, "kg")
    units:
      g:
        factor: 0.001
        aliases: [gram, grams]
      kg:
        factor: 1
        aliases: [kilogram, kilograms]
```

Dimensions require:

- `base`: unit name defined under `units`.
- `units`: non-empty object of unit definitions.

Units require:

- `factor`: numeric multiplier from that unit into the dimension base.
- optional `aliases`: user-facing labels resolved by `centric-api units`.
- optional `basis_units`: implied BOM quantity and width units for material-consumption math.

Consumption sections are optional and describe how material UOMs drive modeling. Unit references in
`output_unit`, `material_value_unit`, and `basis_units` must resolve to known units so modeling
errors fail during config load instead of during a private calculation.

Use `docs/units.md` as the full unit registry reference.
