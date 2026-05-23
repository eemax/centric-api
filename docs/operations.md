# Operations

This guide covers the local files, SQLite state, logs, locks, and recovery paths used by
`centric-api`.

## Runtime Home

By default, runtime state lives under `~/.centric-api`. Set `CENTRIC_API_HOME` to isolate another
environment, such as a test cache or production cache.

Common paths:

| Path | Purpose |
| --- | --- |
| `centric.db` | canonical SQLite cache |
| `local.env` | private credentials |
| `delta.yml` | per-endpoint delta state |
| `raw/runs/` | fetch run evidence and manifests |
| `checkpoints/` | fetch checkpoint files |
| `downloads/files/` | downloaded binary files |
| `downloads/runs/` | download run manifests |
| `bundles/runs/` | bundle manifests and changelogs |
| `bundles/*.zip` | bundle zip archives |
| `logs/fetch.log` | human-readable fetch log |
| `logs/download.log` | human-readable download log |
| `logs/cron.jsonl` | cron JSONL log |

## Locks

Fetch, download, and bundle runs serialize with lock files:

- `fetch.lock`
- `download.lock`
- `bundle.lock`

Dry-run modes skip locks for download and bundle. `fetch --delta-dry-run` also skips the fetch lock.
`cron` uses the fetch lock and logs skipped runs when another fetch is active.

If a process crashes, inspect the stale lock before removing it. `doctor` reports present lock files.

## Fetch And Ingest

Fetch writes raw JSONL evidence first, then ingests successful endpoint files into SQLite. The raw
evidence is the source for `rebuild-db`.

Important behavior:

- Delta fetches append `.delta` raw files and may skip writing empty raw files when the expected
  count is zero.
- Full fetches write empty files for empty endpoints so a successful full snapshot remains
  authoritative.
- A successful full endpoint snapshot removes current local records that are missing from the
  snapshot and writes synthetic hard-delete tombstones.
- Ingest updates `endpoint_records`, `endpoint_tombstones`, and `applied_raw_files`.
- Changelog runs automatically after ingest when current records changed.

Fetch exits nonzero when any selected endpoint fails or when the ingest/changelog pipeline fails.

## Changelog

Changelog stores record-level events and field-level details:

- `endpoint_changelog_runs`
- `endpoint_change_events`
- `endpoint_change_fields`
- `endpoint_change_summary`
- `endpoint_field_change_summary`
- `endpoint_actor_change_summary`
- `endpoint_actor_field_change_summary`

Record-level summaries count records, not fields. Field-level tables are available for detailed churn
analysis.

Removal types:

- `tombstone`: a payload matched endpoint schema delete rules.
- `hard_delete`: a record disappeared from a successful full endpoint snapshot.
- `unknown`: changelog saw a removal without a provided delete reason. This should be rare in the
  normal fetch pipeline.

Actor names resolve from the cached `users` endpoint `node_name`. If users have not been fetched,
actor output may fall back to IDs or `Unknown`.

## Downloads

Download selection reads cached source records, `documents`, and `document_revisions`.

State and artifacts:

- Files are stored under `downloads/files`.
- Run manifests are stored under `downloads/runs`.
- Current state is tracked in `download_current`.
- Run history and item rows are tracked in `download_runs` and `download_items`.

Modes:

- Delta skips current revisions already present on disk.
- Sync verifies selected latest revisions exist.
- Rebuild redownloads selected latest revisions and tombstones current rows that are no longer
  selected.

If a rebuild replacement download fails, the previous known-good current revision is preserved.
Binary downloads retry transient HTTP/server failures with a 15s/30s backoff.

## Bundles

Bundle runs package current downloaded files. Non-dry-run runs write:

- `manifest.json`
- `changelog.json`
- `changelog.md`
- a zip, unless `--no-zip` is used

Bundle state is tracked in:

- `bundle_runs`
- `bundle_items`
- `bundle_current`

Bundle changelog compares bundle runs from the same bundle and reports added, changed, renamed,
removed, and unchanged files. Renames are detected by stable bundle item identity, not just archive
path.

## Status And Doctor

Use `status` for a quick read-only snapshot:

```bash
uv run centric-api status
uv run centric-api status --json
```

Use `doctor` before scheduled runs, after config changes, or when troubleshooting:

```bash
uv run centric-api doctor
uv run centric-api doctor --json
```

`doctor` checks configs, credentials, database schema, dashboard shape, endpoint cache evidence,
download files, bundle/download wiring, and lock files. It exits nonzero on failures and prints
repair hints when available.

## Dashboard Views

SQLite installs stable dashboard views:

- `dashboard_latest_fetch_runs`
- `dashboard_endpoint_state`
- `dashboard_recent_changes`
- `dashboard_actor_activity`
- `dashboard_download_jobs`
- `dashboard_bundle_runs`
- `dashboard_bundle_file_changes`

These are intended for read-only dashboards and ad hoc inspection. `doctor` validates the view shape
and recommends `centric-api rebuild-db --yes` when the local schema is stale.

## Rebuild Recovery

Use `rebuild-db --yes` when the SQLite cache is stale, corrupted, or has an old schema shape:

```bash
uv run centric-api rebuild-db --yes
uv run centric-api rebuild-db --yes --json
```

The command:

1. Backs up the current database, WAL, and SHM files with timestamped suffixes.
2. Replays raw evidence from `CENTRIC_API_HOME/raw` or `--raw-dir`.
3. Rebuilds changelog from current cached records.
4. Reinstalls feature tables and dashboard views.

`rebuild-db` refuses to run without `--yes`.
