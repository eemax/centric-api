# CLI Reference

Run commands with `uv run centric-api ...` from the repository, or `centric-api ...` when installed.
Most commands accept `--db` to point at a non-default SQLite database.

## Output Modes

Human output is the default. `--json` switches to machine-readable output, but the shape varies by
command:

- `fetch --json`, `changelog --json`, `changelog fields --json`, `changelog actors --json`,
  `changelog leaderboard --json`, `changelog runs --json`, `changelog changes --json`, and
  `bundle list --json`, `view list --json`, `load list --json`, `model list --json`,
  `swagger history --json`, `swagger endpoints --json`, `swagger fields --json`, and
  `swagger field --json` emit JSON Lines. `validate list --json` and `validate run all --json`
  also emit JSON Lines.
- `download --json` emits JSON progress records followed by one JSON summary object.
- `changelog update --json`, `bundle run --json`, `bundle show --json`,
  `bundle changelog --json`, `status --json`, `doctor --json`, `rebuild-db --json`,
  `view show --json`, `view check --json`, `view export --json`, `load show --json`,
  `load check --json`, `load run --json`, `load retry --json`, `model show --json`,
  `model check --json`, `model run --json`, `validate show --json`,
  `validate run NAME --json`, `ingest check --json`, `ingest raw-run --json`,
  `map endpoints --json`, `swagger refresh --json`, `swagger status --json`,
  `swagger diff --json`, `swagger coverage --json`, and units commands emit one JSON object.
  `validate history --json` also emits one JSON object with artifact paths and history counts.

Progress lines for fetch and download are written to stderr unless `--quiet` is used. Group config
flags such as `--load-config`, `--models-dir`, `--validators-dir`, and `--units-config` can be
passed either before or after their action.

## Fetch

```bash
uv run centric-api fetch
uv run centric-api fetch --full
uv run centric-api fetch --days 7
uv run centric-api fetch --months 3
uv run centric-api fetch --endpoint styles --endpoint boms
uv run centric-api fetch --delta-dry-run
uv run centric-api fetch --resume
```

`fetch` reads endpoint definitions from `config/fetcher.yml` by default. It writes raw JSONL into a
run directory under `CENTRIC_API_HOME/raw/active`, promotes completed and trusted runs to
`CENTRIC_API_HOME/raw/runs`, quarantines failed endpoint runs under `CENTRIC_API_HOME/raw/failed`,
ingests completed endpoint files into SQLite, and then updates changelog tables for changed records.

Human fetch output starts with run context, including mode, selected endpoint count, raw output
directory, and delta state or explicit modified window. Endpoint progress uses `START`, page, and
`DONE`/`EMPTY` lines; the page line keeps the current page, item, skip, elapsed, progress, average,
and ETA fields with comma-formatted counts. Post-fetch ingest and changelog work is grouped under a
`Pipeline` section with status and elapsed time, followed by a concise stderr run result. The final
human endpoint table includes retry, warning, validation, and elapsed-time columns. Failed human runs
include the fetch log path when logging is enabled.

Example human progress shape:

```text
Fetch run
run=2026-05-27T001500Z-delta  mode=delta  endpoints=2
raw=/path/to/raw/active/2026-05-27T001500Z-delta
delta_state=/path/to/delta.yml  overlap=10m

[styles] START  expected=1,000  limit=50  skip=0  retries=0  delta_floor=2026-05-26T23:50:00Z  elapsed=420ms
[styles] page 1/20: page_items=50 total_items=50 skip=0 next_skip=50 elapsed=0.81s progress=5.0% avg_page=810ms eta=15.4s
[styles] DONE   items=1,000/1,000  pages=20  retries=0  warnings=0  elapsed=16.4s

Pipeline
manifest=writing
manifest=ok path=/path/to/raw/runs/2026-05-27T001500Z-delta/manifest.json
ingest=running
ingest=ok records_read=1,000 upserts=980 deletes=20
changelog=running
changelog=ok events=84 scoped=1,000
pipeline=done ingest=ok changelog=ok elapsed=2.1s

Fetch result
status=ok endpoints=2/2 records=1,000 pages=20 retries=0 elapsed=18.5s
```

Modes:

- Default delta mode derives an `_modified_at=ge` floor from `delta.yml`.
- `--full` fetches complete endpoint snapshots and can generate hard-delete tombstones for records
  missing from a successful full endpoint snapshot.
- `--days N` and `--months N` run explicit `_modified_at` windows.
- `--delta-dry-run` prints the derived delta filters without taking the fetch lock, fetching, logging,
  ingesting, or updating changelog.
- `--resume` resumes from checkpoint files when possible.

Raw run lifecycle:

- `raw/active/RUN_ID`: in-progress or interrupted fetch with `.running.json`.
- `raw/runs/RUN_ID`: completed and trusted raw evidence with `.completed.json`; this is the default
  source for `ingest raw-run RUN_ID` and `rebuild-db`.
- `raw/failed/RUN_ID`: failed or partial fetch evidence with `.failed.json`; this is intentionally
  skipped by raw-root ingest/rebuild discovery and can be inspected or deleted as a group.

Useful options:

- `--fetch-config PATH`: fetcher config; default is `config/fetcher.yml`.
- `--endpoint NAME`: repeatable endpoint filter.
- `--schema PATH`: endpoint tombstone schema overlay.
- `--delta-state-file PATH`: override private `delta.yml`.
- `--env-file PATH`: override credential env file.
- `--quiet`: suppress human progress and summary output.
- `--log-level summary|http|debug|off`: controls `logs/fetch.log`.

Fetch uses `CENTRIC_API_HOME/fetch.lock`. A successful fetch writes a run manifest alongside raw
files. The process exits nonzero if any selected endpoint fails or the ingest/changelog pipeline
fails.

## Map

```bash
uv run centric-api map endpoints
uv run centric-api map endpoints --output-dir /path/to/maps
uv run centric-api map endpoints --json
```

`map endpoints` inspects cached endpoint payloads and infers references by matching payload values
to cached record IDs. It writes all artifacts for the run:

```text
CENTRIC_API_HOME/maps/endpoints/{run_id}/relationships.json
CENTRIC_API_HOME/maps/endpoints/{run_id}/endpoint-map.md
CENTRIC_API_HOME/maps/endpoints/{run_id}/endpoint-map.html
```

The Markdown file is intended as a compact agent-readable map. The HTML file is a static local
explorer for inspecting incoming and outgoing endpoint relationships.

## Swagger

```bash
uv run centric-api swagger refresh
uv run centric-api swagger status
uv run centric-api swagger history
uv run centric-api swagger history --diffs
uv run centric-api swagger endpoints
uv run centric-api swagger fields --endpoint styles --method get
uv run centric-api swagger field 42
uv run centric-api swagger field --endpoint styles custom_style_brand_category_enum
uv run centric-api swagger diff
uv run centric-api swagger diff --history 0 1
uv run centric-api swagger coverage
```

`swagger` is optional local API-schema tooling. It reads and writes:

```text
CENTRIC_API_HOME/swagger/current.json
CENTRIC_API_HOME/swagger/current.meta.json
CENTRIC_API_HOME/swagger/history/{snapshot_id}.json
CENTRIC_API_HOME/swagger/history/{snapshot_id}.meta.json
```

Actions:

- `refresh`: fetches `CENTRIC_BASE_URL/api/v2/swagger.json`, writes the local Swagger JSON, and
  records metadata including source URL, fetch time, SHA-256, operation count, endpoint count,
  field counts, and the last field/operation diff versus the previous local file. Each refresh also
  writes an immutable timestamped schema and metadata snapshot under `swagger/history/`.
- `status`: reports whether the local Swagger JSON and metadata exist, plus freshness and counts
  when available.
- `history`: lists timestamped snapshots newest first. Index `0` is the latest snapshot, index `1`
  is the previous snapshot, and so on. Use `--diffs` to show adjacent drift counts across the
  saved snapshots, such as `0` compared with `1`, `1` compared with `2`, and onward.
- `endpoints`: lists normalized Swagger methods and paths with request/response schema names and
  field counts. Use `--endpoint NAME` to filter by root endpoint.
- `fields`: lists request and response payload fields with a global field index. Use
  `--endpoint NAME` and `--method get|post|put|patch|delete|all`; the default method is `get`.
  When `--endpoint` is set, only the root endpoint path is shown unless `--include-nested` is
  passed. Use `--required-only` to focus on required payload fields.
- `field`: inspects one field without truncating enum values. Without `--endpoint`, the selector
  must be a global field index copied from `swagger fields`, such as `swagger field 42`. With
  `--endpoint`, the selector is interpreted as a field name, such as
  `swagger field --endpoint styles custom_style_brand_category_enum`; numeric-looking names remain
  names in this mode. Use `--method` or `--include-nested` to narrow endpoint-scoped name matches.
- `diff`: shows field-first schema drift from metadata, compares the current Swagger against another
  file with `--against PATH`, or compares two history snapshots with
  `--history CURRENT_INDEX BASELINE_INDEX`. History indexes are newest first, so `--history 0 1`
  means "show what changed in the latest snapshot compared with the previous snapshot." Human diff
  output intentionally does not truncate changed field values or enum values. Use `--endpoint NAME`,
  `--method get|post|put|patch|delete|all`, `--include-nested`, `--fields-only`, or
  `--operations-only` to focus the report.
- `coverage`: compares top-level Swagger GET collection paths with `config/fetcher.yml`, or another
  config passed with `--fetch-config`. Human output lists covered endpoints, configured endpoints
  missing from Swagger, and all Swagger-only collections with response/POST field counts.

The current Swagger and metadata files live under `CENTRIC_API_HOME/swagger/`. Swagger is an
auditor, not the runtime source of truth: fetch/load config remains authoritative, and Swagger
commands are meant to catch drift early.

## Changelog

```bash
uv run centric-api changelog
uv run centric-api changelog fields
uv run centric-api changelog actors
uv run centric-api changelog leaderboard
uv run centric-api changelog runs
uv run centric-api changelog changes
uv run centric-api changelog update
```

Common options:

- `--since VALUE`: relative `10m`, `24h`, `7d`, or an ISO timestamp. Changelog activity views use
  Centric `_modified_at`; `runs` uses local changelog run creation time.
- `--endpoint NAME`: filters to one endpoint for read views; `update` accepts repeatable endpoints.
  `runs` does not support endpoint filtering.
- `--limit N`: limits displayed rows or actors, depending on the action.
- `--json`: emits JSON Lines for read views; `update --json` emits one JSON object.

Actions:

- `summary` (default): endpoint totals plus top modified-by actors.
- `fields`: field-level rollups. Without `--endpoint`, shows endpoint-level field activity; with
  `--endpoint`, shows field-event counts for that endpoint.
- `actors`: operational actor-by-endpoint table.
- `leaderboard`: ranked actor activity view. Score is records touched, not fields touched: one added
  record, one changed record, and one removed record each count as one. Human output shows a ranked
  actor table followed by endpoint breakdowns for the displayed actors. `--limit` limits actors only;
  endpoint breakdowns stay complete for those actors.
- `runs`: changelog run history.
- `changes`: recent event rows with changed-field summaries.
- `update`: rebuilds changelog from current cached records. Human output shows progress through the
  long-running phases; `--json` keeps stdout to a single summary object. This is normally automatic
  after fetch.

Removal breakdowns are always present in `leaderboard --json` as `tombstone`, `hard_delete`, and
`unknown_delete`. Human output keeps tombstone-only removals folded into `Removed`; it adds `Tomb`,
`Hard`, or `Unknown` columns only when the displayed rows need them.

## Ingest

```bash
uv run centric-api ingest check 2026-06-16T124501Z-full
uv run centric-api ingest check /path/to/raw/runs/2026-06-16T124501Z-full --db scratch.db
uv run centric-api ingest raw-run 2026-06-16T124501Z-full
uv run centric-api ingest raw-run 2026-06-16T124501Z-full --changelog
```

`ingest` is an operator command for already-captured raw evidence. It does not fetch from Centric.
`RAW_RUN` can be either a run id under `CENTRIC_API_HOME/raw/runs` or an explicit run directory path.
Raw runs must include `manifest.json`.

Actions:

- `check`: validates the raw-run manifest, listed JSONL files, JSONL parseability, lifecycle state,
  endpoint schema coverage, and whether each file is new, already applied, or drifted in the
  selected DB.
- `raw-run`: applies one completed raw run to SQLite. With `--changelog`, it runs the normal scoped
  changelog from the ingest result and skips changelog clearly when the raw files were already
  applied. Explicit `raw/active` and `raw/failed` paths are inspectable with `check`, but refused by
  `raw-run`.

Useful options:

- `--db PATH`: target SQLite database; defaults to `CENTRIC_API_HOME/centric.db`.
- `--schema PATH`: endpoint tombstone schema overlay.
- `--json`: emits one JSON object.

## Download

```bash
uv run centric-api download
uv run centric-api download --job basic
uv run centric-api download --dry-run
uv run centric-api download --sync
uv run centric-api download --rebuild
```

`download` selects documents from the local SQLite cache and downloads each selected latest revision
through `document_revisions/{revision_id}/download`.

Modes:

- Default delta mode skips revisions already marked current and present on disk with matching
  recorded metadata.
- `--sync` verifies selected latest revisions exist without overwriting files that still match
  recorded metadata.
- `--rebuild` redownloads selected latest revisions and tombstones current rows no longer selected.
- `--dry-run` performs selection only, skips the download lock, and writes no download state.

Useful options:

- `--download-config PATH`: default resolution is private `CENTRIC_API_HOME/download.yml`, then
  `config/download.yml`.
- `--job NAME`: required when the config has multiple jobs.
- `--fetch-config PATH` and `--env-file PATH`: used for credentials when downloading.
- `--log-level summary|http|debug|off`: controls `logs/download.log` for non-dry-run downloads.

Non-dry-run downloads use `CENTRIC_API_HOME/download.lock`. They write files under
`downloads/files`, manifests under `downloads/runs`, and state into `download_*` SQLite tables.

## Bundle

```bash
uv run centric-api bundle
uv run centric-api bundle run --job basic
uv run centric-api bundle run --dry-run
uv run centric-api bundle run --no-zip
uv run centric-api bundle list
uv run centric-api bundle show BUNDLE_RUN_ID
uv run centric-api bundle changelog FROM_BUNDLE_RUN_ID
uv run centric-api bundle changelog FROM_BUNDLE_RUN_ID --to TO_BUNDLE_RUN_ID
```

`bundle` packages current downloaded files for distribution. `centric-api bundle` is normalized to
`centric-api bundle run`.

Run options:

- `--bundle-config PATH`: default resolution is private `CENTRIC_API_HOME/bundle.yml`, then
  `config/bundle.yml`.
- `--job NAME`: required when the config has multiple bundles.
- `--dry-run`: checks selection and planned artifacts without writing bundle state or files.
- `--no-zip`: writes run artifacts without creating a zip.

History commands:

- `bundle list`: recent bundle runs, optionally filtered by `--job`.
- `bundle show BUNDLE_RUN_ID`: run metadata and up to 50 files in human output.
- `bundle changelog FROM_BUNDLE_RUN_ID`: compares a received run with the latest later run of the
  same bundle. Use `--to` for an exact comparison target.

Non-dry-run bundle runs use `CENTRIC_API_HOME/bundle.lock`, write run artifacts under
`bundles/runs`, create zips under `bundles/` by default, and track state in `bundle_*` SQLite tables.

## View Exports

```bash
uv run centric-api view list
uv run centric-api view show style-colorways-demo
uv run centric-api view check style-colorways-demo
uv run centric-api view export style-colorways-demo
uv run centric-api view export style-colorways-demo --format csv
uv run centric-api view export style-colorways-demo --output ~/Desktop/style-colorways.xlsx
```

`view export` writes flat XLSX or CSV tables from local cached endpoint records or calculated model
output tables. It does not call the Centric API. The root source plus any `many_expand` joins define
row grain; supplementary arrays should use `many_concat`. Filters live in the view schema and can
reference root or joined aliases.
When joins cannot resolve, the export summary includes a per-join breakdown with the joined
source, join paths, missing counts, and sample reference keys.
Use `view check NAME` to run the same materialization and reference diagnostics without writing a
file.

Options:

- `--view-config PATH`: default resolution is private `CENTRIC_API_HOME/views.yml`, then
  `config/views.yml`.
- `--db PATH`: SQLite cache to read.
- `--format xlsx|csv`: output format. Defaults to `xlsx`, or is inferred from `--output`.
- `--output PATH`: output file path. Defaults to `CENTRIC_API_HOME/exports/{view}-{timestamp}.xlsx`.
- `--json`: machine-readable output.

See [View exports](views.md) for the schema contract and authoring rules.

## Validation

```bash
uv run centric-api validate list
uv run centric-api validate show my-validator
uv run centric-api validate run my-validator
uv run centric-api validate run all
uv run centric-api validate history
uv run centric-api validate history --group month
```

`validate` runs private cache validation modules and writes artifacts instead of database history
tables. A run writes `report_<YY-MM-DD-HHMM>.xlsx`, `summary.json`, `findings.json`, and
`history.json` under
`CENTRIC_API_HOME/validation/runs/<validator>/<run-id>/`.
`validate history` refreshes `CENTRIC_API_HOME/validation/history/history.html`,
and `history.json` from first-class run `history.json` files.

Useful options:

- `--validators-dir PATH`: load private validators from a specific directory.
- `--units-config PATH`: use an explicit unit registry.
- `--db PATH`: use a non-default SQLite cache for `run`.
- `--output-dir PATH`: choose the validation artifact root.
- `--group day|week|month`: choose `validate history` grouping; default is `week`.
- `--validator NAME`: filter `validate history`; repeat for multiple validators.
- `--json`: emit machine-readable output.

There are no bundled validators. Validator modules load from `CENTRIC_API_HOME/validators/*.py` by
default, or from `--validators-dir PATH`. See [Validation](validation.md) for the validator contract,
context helpers, artifact shape, and private authoring guidance.

## Snapshots

```bash
uv run centric-api snapshot list
uv run centric-api snapshot show dpp
uv run centric-api snapshot check dpp
uv run centric-api snapshot build dpp
uv run centric-api snapshot build dpp --target baseline
uv run centric-api snapshot promote dpp
uv run centric-api snapshot build dpp --output-dir ~/review-repos/snapshots
```

`snapshot` runs private cache modeling/grouping modules and writes deterministic JSONL directories
for Git review workflows. Builds write to a snapshot workspace target: `candidate` by default, or
`baseline` when explicitly selected. `snapshot promote` copies reviewed candidate artifacts to
baseline exactly. There are no bundled snapshots. Private modules load from
`CENTRIC_API_HOME/snapshots/*.py` by default, or from `--snapshots-dir PATH`.

Useful options:

- `--snapshots-dir PATH`: load private snapshots from a specific directory.
- `--units-config PATH`: use an explicit unit registry.
- `--db PATH`: use a non-default SQLite cache for `check` or `build`.
- `--output-dir PATH`: choose the snapshot workspace root for `build`.
- `--target candidate|baseline`: choose the workspace target for `build`; default is `candidate`.
- `promote`: copy `candidate/` to `baseline/` after review.
- `--clean`: replace non-hidden contents in the selected snapshot target while preserving hidden
  directories such as `.git`.
- `--json`: emit machine-readable output.

See [Snapshots](snapshot.md) for the private module contract and artifact rules.

## Load Jobs

```bash
uv run centric-api load list
uv run centric-api load list --load-config ./load.yml
uv run centric-api load show material-create
uv run centric-api load check material-create materials.xlsx
uv run centric-api load check material-create materials.xlsx --sheet Materials
uv run centric-api load check material-composition-create material-compositions.xlsx
uv run centric-api load run material-create-with-composition-and-quote materials.xlsx --dry-run
uv run centric-api load run style-bom-load style-bom-lines.xlsx --dry-run
uv run centric-api load run style-supplier-quote-load style-supplier-quotes.xlsx --dry-run
uv run centric-api load run material-supplier-quote-load material-supplier-quotes.xlsx --dry-run
uv run centric-api load run material-create materials.xlsx --dry-run
uv run centric-api load run material-create materials.xlsx --yes
uv run centric-api load retry material-create review.xlsx --dry-run
uv run centric-api load retry material-create review.xlsx --yes
```

`load` validates spreadsheet rows and can send API requests to Centric. The bundled
`material-create` job reads Excel rows, resolves `Product Type`/`Material Type` through cached
`material_types`, and posts valid rows to `/v2/materials`. The bundled
`material-composition-create` job accepts either
material IDs or material codes, parses natural-language composition text, and posts technical
compositions to `/v2/materials/{material}/technical_compositions`.
`material-create-with-composition` chains material creation with composition creation for new
materials. `material-create-with-composition-and-quote` also chains material supplier quote creation
for the new material. The bundled `style-bom-load` workflow validates a style within a season, then
chains BOM header creation, owned section creation, and material line creation from one workbook. The
bundled `style-supplier-quote-load` workflow validates a style within a season, optional
supplier-agent membership, and optional supplier-factory membership, then chains product source,
supplier item, quote factory, and optional production quote updates for styles.
`material-supplier-quote-load` uses the same supplier quote chain for materials resolved by code
and can set the material's default quote. If
`--sheet` is omitted, the first worksheet is used.
Real runs write a review workbook when rows receive API outcomes or validation errors; `retry`
processes rows in a review workbook with `_cent_load_status` of `failed` or `validation_error`.

`load list` shows whether each job is `bundled`, `private`, or `explicit`. Private jobs from
`CENTRIC_API_HOME/load.yml` replace bundled jobs with the same name.

Options:

- `--load-config PATH`: default resolution is `config/load.yml` plus private
  `CENTRIC_API_HOME/load.yml`.
- `--sheet NAME`: worksheet to read. Defaults to the first worksheet.
- `--limit N`: process only the first N non-empty data rows.
- `--db PATH`: SQLite cache used for reference resolution.
- `--dry-run`: write planned request artifacts without API calls.
- `--yes`: required for real API writes.
- `--env-file PATH`: credential file for real API writes.
- `--statuses LIST`: retry statuses to process. Defaults to `failed,validation_error`.
- `--quiet`: suppress human progress lines.
- `--json`: machine-readable output.

See [Load jobs](load.md) for the schema contract and safety rules.

## Models

```bash
uv run centric-api model list
uv run centric-api model show my-model
uv run centric-api model check my-model
uv run centric-api model run my-model
```

`model` loads private Python model modules from `CENTRIC_API_HOME/models`. Models read the local
SQLite cache, validate business inputs, and refresh stable calculated output tables. Successful runs
replace the current output table; failed runs leave the previous output table intact.

Options:

- `--models-dir PATH`: load models from a specific directory.
- `--units-config PATH`: use an explicit unit registry.
- `--db PATH`: SQLite cache for `check` or `run`.
- `--json`: machine-readable output.

Group config flags such as `--models-dir` and `--units-config` may be passed before or after the
model action.

See [Modeling](modeling.md) for the private model interface.

## Units

```bash
uv run centric-api units list
uv run centric-api units list --units-config ./units.yml
uv run centric-api units show mass
uv run centric-api units normalize "sq m"
uv run centric-api units convert 1500 g kg
uv run centric-api units basis gsm
uv run centric-api units check
```

`units` validates and uses the local unit registry. The default registry is `config/units.yml`;
private `CENTRIC_API_HOME/units.yml` extends the defaults when present. Pass `--units-config PATH`
before the units action to use an explicit registry.
`units basis UNIT` shows the material-consumption formula implied by material UOMs such as `pcs`,
`kg`, `gsm`, or `g/m`.

See [Units](units.md) for registry authoring rules.

## Status And Doctor

```bash
uv run centric-api status
uv run centric-api status --json
uv run centric-api doctor
uv run centric-api doctor --json
```

`status` is read-only and summarizes runtime home, DB path, locks, Swagger freshness, latest
fetch/changelog/download/bundle runs, and cached endpoint counts.

`doctor` validates local setup:

- fetch, schema, download, and bundle configs
- credential presence
- SQLite schema version and required tables
- dashboard schema shape
- local Swagger presence and freshness
- cached endpoints required by download jobs
- current downloaded files on disk
- bundle/download wiring
- lock files

`doctor` exits nonzero if any check fails. Human output includes repair hints when available.

## Cron

```bash
uv run centric-api cron
uv run centric-api cron "0 * * * *"
uv run centric-api cron "*/15 * * * *" --run-now --endpoint styles
```

`cron` runs foreground scheduled delta fetches. It accepts five-field cron schedules and defaults to
hourly. Each run executes fetch in quiet JSON mode with fetch logging disabled, writes JSONL records
to `logs/cron.jsonl`, and uses the fetch lock to avoid overlapping fetches.

## Rebuild DB

```bash
uv run centric-api rebuild-db --yes
uv run centric-api rebuild-db --yes --raw-dir ~/.centric-api/raw
uv run centric-api rebuild-db --yes --json
```

`rebuild-db` is the recovery path for SQLite. It refuses to run without `--yes`, backs up the current
database files, replays raw evidence into a fresh database, rebuilds changelog, and reinstalls
dashboard views. Human output shows progress through the long-running rebuild phases; `--json`
keeps stdout to a single summary object.
