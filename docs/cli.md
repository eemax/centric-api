# CLI Reference

Run commands with `uv run centric-api ...` from the repository, or `centric-api ...` when installed.
Most commands accept `--db` to point at a non-default SQLite database.

## Output Modes

Human output is the default. `--json` switches to machine-readable output, but the shape varies by
command:

- `fetch --json`, `changelog --json`, `changelog fields --json`, `changelog actors --json`,
  `changelog leaderboard --json`, `changelog runs --json`, `changelog changes --json`, and
  `bundle list --json` emit JSON Lines.
- `download --json` emits JSON progress records followed by one JSON summary object.
- `bundle run --json`, `bundle show --json`, `bundle changelog --json`, `status --json`,
  `doctor --json`, and `rebuild-db --json` emit one JSON object.

Progress lines for fetch and download are written to stderr unless `--quiet` is used.

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
run directory under `CENTRIC_API_HOME/raw/runs`, ingests successful endpoint files into SQLite, and
then updates changelog tables for changed records.

Modes:

- Default delta mode derives an `_modified_at=ge` floor from `delta.yml`.
- `--full` fetches complete endpoint snapshots and can generate hard-delete tombstones for records
  missing from a successful full endpoint snapshot.
- `--days N` and `--months N` run explicit `_modified_at` windows.
- `--delta-dry-run` prints the derived delta filters without taking the fetch lock, fetching, logging,
  ingesting, or updating changelog.
- `--resume` resumes from checkpoint files when possible.

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

- `--since VALUE`: relative `10m`, `24h`, `7d`, or an ISO timestamp.
- `--endpoint NAME`: filters to one endpoint for read views; `update` accepts repeatable endpoints.
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
- `update`: rebuilds changelog from current cached records. This is normally automatic after fetch.

Removal breakdowns are always present in `leaderboard --json` as `tombstone`, `hard_delete`, and
`unknown_delete`. Human output keeps tombstone-only removals folded into `Removed`; it adds `Tomb`,
`Hard`, or `Unknown` columns only when the displayed rows need them.

## Download

```bash
uv run centric-api download
uv run centric-api download --job ss26-style-techpacks
uv run centric-api download --dry-run
uv run centric-api download --sync
uv run centric-api download --rebuild
```

`download` selects documents from the local SQLite cache and downloads each selected latest revision
through `document_revisions/{revision_id}/download`.

Modes:

- Default delta mode skips revisions already marked current and present on disk.
- `--sync` verifies selected latest revisions exist without overwriting present files.
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
uv run centric-api bundle run --job ss26-style-techpacks
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

## Status And Doctor

```bash
uv run centric-api status
uv run centric-api status --json
uv run centric-api doctor
uv run centric-api doctor --json
```

`status` is read-only and summarizes runtime home, DB path, locks, latest fetch/changelog/download/
bundle runs, and cached endpoint counts.

`doctor` validates local setup:

- fetch, schema, download, and bundle configs
- credential presence
- SQLite schema version and required tables
- dashboard schema shape
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
dashboard views.
