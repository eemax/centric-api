# centric-api

Runtime state is stored in `~/.centric-api` by default. Set `CENTRIC_API_HOME` to use a different
directory.

Docs:

- [CLI reference](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Deployment](docs/deployment.md)
- [Load jobs](docs/load.md)
- [Modeling spec](docs/modeling.md)
- [Operations](docs/operations.md)
- [Units](docs/units.md)
- [Validation](docs/validation.md)
- [View exports](docs/views.md)

```bash
uv run centric-api fetch
uv run centric-api fetch --full
uv run centric-api fetch --days 7
uv run centric-api fetch --fetch-config config/fetcher.yml
uv run centric-api changelog
uv run centric-api download --dry-run
uv run centric-api download --sync
uv run centric-api download --rebuild
uv run centric-api bundle
uv run centric-api bundle --job basic
uv run centric-api bundle list
uv run centric-api bundle show BUNDLE_RUN_ID
uv run centric-api bundle changelog BUNDLE_RUN_ID
uv run centric-api view list
uv run centric-api view check style-colorways-demo
uv run centric-api view export style-colorways-demo
uv run centric-api load check material-create materials.xlsx
uv run centric-api load run material-create materials.xlsx --dry-run
uv run centric-api load retry material-create review.xlsx --dry-run
uv run centric-api model list
uv run centric-api model check my-model
uv run centric-api validate list
uv run centric-api validate run my-validator
uv run centric-api validate history
uv run centric-api units convert 1500 g kg
uv run centric-api units basis gsm
uv run centric-api status
uv run centric-api swagger refresh
uv run centric-api swagger status
uv run centric-api swagger history
uv run centric-api swagger history --diffs
uv run centric-api swagger endpoints
uv run centric-api swagger fields --endpoint styles --method get
uv run centric-api swagger field 42
uv run centric-api swagger diff
uv run centric-api swagger diff --history 0 1
uv run centric-api swagger coverage
uv run centric-api doctor
uv run centric-api ingest check 2026-06-16T124501Z-full
uv run centric-api ingest raw-run 2026-06-16T124501Z-full --changelog
uv run centric-api rebuild-db --yes
uv run centric-api cron
uv run centric-api cron "0 * * * *" --endpoint styles
```

`fetch` defaults to delta mode. `--full` refetches all configured endpoints and still advances the
delta state after successful endpoint fetches. `--days` and `--months` run explicit `_modified_at`
windows.

Raw JSONL, checkpoints, logs, `delta.yml`, and the canonical SQLite cache live under
`~/.centric-api`. Fetches write in-progress evidence under `raw/active`, promote completed and
trusted runs to `raw/runs`, and quarantine failed runs under `raw/failed`. The local database
defaults to `~/.centric-api/centric.db`.
Manual fetches append human-readable run logs to `~/.centric-api/logs/fetch.log` by default. The
default `--log-level summary` writes run, endpoint, ingest, and changelog summary lines. Use
`--log-level http` to include API request/response diagnostics, `--log-level debug` for
checkpoint/resume internals, or `--log-level off` to disable the log.

`cron` runs in the foreground until stopped and defaults to hourly (`0 * * * *`). It prints
scheduler lifecycle messages and concise fetch summaries to the terminal, runs fetch in quiet JSON
mode, and writes JSONL-only records to `~/.centric-api/logs/cron.jsonl`. Fetch runs are serialized
with `~/.centric-api/fetch.lock`. For unattended Linux servers, prefer the systemd timer examples in
[Deployment](docs/deployment.md).

`download` selects document records from the local SQLite cache and downloads each selected
document's `latest_revision` through `document_revisions/{revision_id}/download`. Source endpoint
`documents` selects document records directly; every other source endpoint automatically collects
document IDs from `documents` and `referenced_documents`. Filter document metadata with
`document_filters`, and filter real revision metadata such as filename with `revision_filters`
against the cached `document_revisions` endpoint. Source filters also support a narrow `lookup`
operator for single reference IDs, such as filtering styles by the referenced season's `node_name`.
Download preflight fails before selection if a job's source endpoints, `document_revisions`
dependency, or lookup endpoints have not been fetched into the local cache yet. The default mode is
delta: documents already marked current in SQLite and present on disk are
skipped. Delta and sync modes trust already-present files only when their on-disk metadata still
matches the recorded current download state. `--sync` verifies all selected latest revisions exist
without overwriting files that still match recorded state. `--rebuild` redownloads selected latest
revisions and tombstones current download rows that are no longer selected, while preserving the last
known good current revision if a replacement download fails. Non-dry-run download runs are
serialized with
`CENTRIC_API_HOME/download.lock`, and binary downloads retry transient HTTP/server hiccups with a
simple 15s/30s backoff. The default config is `config/download.yml`, with a fuller multi-job example
in `config/download.example.yml`; place `download.yml` in `CENTRIC_API_HOME` for private jobs, or
pass `--download-config`. Files are stored under
`CENTRIC_API_HOME/downloads/files`, non-dry-run runs write manifests under
`CENTRIC_API_HOME/downloads/runs`, current download state is tracked in the `download_current` SQLite
table, and human-readable download logs append to `CENTRIC_API_HOME/logs/download.log` for
non-dry-run runs.

`bundle` packages already-downloaded current files for distribution. Bundle jobs live in
`config/bundle.yml` or private `CENTRIC_API_HOME/bundle.yml`, point at a `download_job`, and use a
human-friendly archive layout of `files/{source_endpoint}/{source_label}/{filename}`. Source labels
default to `node_name` and can be configured per endpoint by concatenating fields such as
`style_code` and `node_name`. If the same document is referenced by multiple selected source
objects, the bundle includes one copy under each source object folder. Non-dry-run runs write
`manifest.json`, `changelog.json`, and `changelog.md`, then create a zip by default. Bundle
changelog compares against the previous successful run of the same bundle and reports added,
changed, renamed, removed, and unchanged files. Bundle state is tracked in SQLite, and non-dry-run
runs are serialized with `CENTRIC_API_HOME/bundle.lock`.

Bundle run IDs are timestamp-based and are the precise anchor for distribution support. Use
`centric-api bundle list` to see past distributions, `centric-api bundle show BUNDLE_RUN_ID` to
inspect one, and `centric-api bundle changelog FROM_BUNDLE_RUN_ID` to compare a received bundle
against the latest later run of the same bundle. Pass `--to BUNDLE_RUN_ID` for an exact comparison
target.

`view export` turns cached endpoint records or calculated model output tables into flat XLSX or CSV
tables using configured view schemas. The repo includes `config/views.yml` as a demo; production
schemas normally live in private `CENTRIC_API_HOME/views.yml` or are passed with `--view-config`.
Views are read-only and local: they do not call the Centric API.

`validate` runs private cache validation reports and writes timestamped artifacts under
`CENTRIC_API_HOME/validation/runs`. The main repo provides the command, cache helpers, and standard
`report.xlsx`, `summary.json`, `findings.json`, and `history.json` artifact writer. The
`validate history` command refreshes HTML, XLSX, and JSON history output from those first-class
history metrics. Validators that should trend over time should emit explicit aggregated
`ValidationHistoryMetric` values; use one metric per trend series, include `numerator` and
`denominator` for percentages, and emit both overall and per-brand metric sets when brand comparison
matters. Validation logic lives in private `CENTRIC_API_HOME/validators` modules or a directory
passed with `--validators-dir`.

`load` validates workbook rows and can send API requests to Centric. The repo includes
`material-create`, which posts material rows to `/v2/materials`, and
`material-composition-create`, which parses composition text like `95% cotton, 5% polyester` and
posts technical compositions to existing materials. `material-create-with-composition` chains those
two steps for new materials, and `material-create-with-composition-and-quote` also creates a
material supplier quote in the same row. It also includes `style-bom-load`, which validates style
and season together, then chains BOM header, section, and material-line creation from one workbook.
`PM ID` and `Quantity` are optional for BOM line loads. It also includes
`style-supplier-quote-load`, which chains product source, supplier item, optional quote factory,
and optional production quote updates for styles; `material-supplier-quote-load` does the same
supplier quote chain for materials, resolves the material from `Material Code`, and can set the
material's default quote. `Agent` is optional on the product source.
Use `load check` and `load run --dry-run` before running with `--yes`. Real runs write a
`review.xlsx` copy for row-level success, failure, and validation status; `load retry` reprocesses
failed or validation-error rows from that review workbook. Load schemas live in `config/load.yml`
plus private `CENTRIC_API_HOME/load.yml`.

`status` gives a quick read-only overview of runtime home, DB path, locks, Swagger freshness, latest
fetch/changelog, download, bundle, and endpoint counts. `doctor` validates local setup, config,
credentials presence, SQLite state, local Swagger freshness, cached endpoints required by download
jobs, bundle/download wiring, stale locks, and missing current download files. It exits nonzero when
any check fails.

`swagger` is optional local API-schema tooling. `swagger refresh` fetches the Centric Swagger JSON
from `CENTRIC_BASE_URL/api/v2/swagger.json`, writes `CENTRIC_API_HOME/swagger/current.json`, records
freshness, SHA-256, operation/field counts, and the last field/operation diff in
`CENTRIC_API_HOME/swagger/current.meta.json`, and stores timestamped snapshots under
`CENTRIC_API_HOME/swagger/history/`. `swagger history` lists snapshots newest first, so
`swagger diff --history 0 1` compares the previous snapshot against the latest snapshot, and
`swagger history --diffs` shows adjacent snapshot drift counts across the stored history. `swagger
endpoints` lists normalized paths and methods, `swagger fields` inspects request/response payload
fields by endpoint and HTTP method with copy/paste field indexes, `swagger field` inspects one
field by global index or endpoint-scoped name with full enum values, `swagger diff` shows full
field-first schema drift plus operation drift without truncating changed values, and
`swagger coverage` compares Swagger GET collection paths with the configured fetch endpoints while
showing covered and Swagger-only schema field counts.

`ingest` is the operator path for already-captured raw evidence. `ingest check RAW_RUN` validates a
run directory under `CENTRIC_API_HOME/raw/runs` or an explicit path, checks JSONL readability,
reports lifecycle state, and reports whether its files are already applied to the selected DB.
`ingest raw-run RAW_RUN` applies only completed evidence to SQLite; pass `--changelog` to run the
normal scoped changelog from the ingest result.

`rebuild-db --yes` is the SQLite recovery path. It backs up the current SQLite database files,
replays completed raw evidence from `CENTRIC_API_HOME/raw/runs` into a fresh DB, rebuilds changelog,
and reinstalls dashboard views. Use `--raw-dir` or `--db` to override the defaults.

If `~/.centric-api/delta.yml` does not exist, the first delta fetch starts with no floor, so it
fetches all configured records and writes the delta state after successful endpoint fetches. To seed
the file manually, copy `config/delta.example.yml` to `~/.centric-api/delta.yml`. Delta fetches
default to a 10-minute overlap from the previous successful fetch start.

Credentials resolve from environment variables or `~/.centric-api/local.env`:

```bash
CENTRIC_BASE_URL=your-brand
CENTRIC_USERNAME=user@example.com
CENTRIC_PASSWORD=secret
```

`CENTRIC_BASE_URL` accepts a brand slug such as `your-brand`, a Centric host such as
`https://your-brand.centricsoftware.com`, or the full request-handler root. Centric Software hosts
normalize to `https://your-brand.centricsoftware.com/csi-requesthandler` internally.

Endpoint schema is intentionally lean. Record identity is always `id`, and freshness is always
`_modified_at`; schema files only define endpoint-specific tombstone rules:

```yaml
endpoints:
  styles:
    delete_when_any:
      - field: active
        equals: false
```

Private schema overlays resolve from `~/.centric-api/endpoint-schema.yml`.

Changelog tracking is automatic. It compares canonical full payloads for current records and records
added, changed, and removed events after ingest. Event rows keep the previous and current payloads
for drill-down, plus delete type for removals and actor fields from `modified_by`. Actor names are
resolved from the `users` endpoint `node_name`.
Changelog activity filters such as `--since 7d` use Centric `_modified_at` rather than the local
fetch or changelog detection time. `changelog runs --since` remains based on local run creation time.

Full fetch ingest is authoritative per successful endpoint. Current local records missing from a
successful full snapshot are removed from `endpoint_records` and written as synthetic hard-delete
tombstones.

For dashboard-style queries, changelog also writes field-level rows and compact rollups:
`endpoint_change_summary`, `endpoint_field_change_summary`, `endpoint_actor_change_summary`, and
`endpoint_actor_field_change_summary`. SQLite also exposes stable dashboard views:
`dashboard_latest_fetch_runs`, `dashboard_endpoint_state`, `dashboard_recent_changes`,
`dashboard_actor_activity`, `dashboard_download_jobs`, `dashboard_bundle_runs`, and
`dashboard_bundle_file_changes`. Use `centric-api changelog fields`,
`centric-api changelog actors`, and `centric-api changelog leaderboard` for quick aggregate views.
