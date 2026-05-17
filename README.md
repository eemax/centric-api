# centric-api

Centric API fetcher, local SQLite cache, and endpoint changelog.

Runtime state is stored in `~/.centric-api` by default. Set `CENTRIC_API_HOME` to use a different
directory.

```bash
uv run centric-api fetch
uv run centric-api fetch --full
uv run centric-api fetch --days 7
uv run centric-api fetch --fetch-config config/fetcher.yml
uv run centric-api changelog
uv run centric-api download --dry-run
uv run centric-api download --sync
uv run centric-api download --rebuild
uv run centric-api cron
uv run centric-api cron "0 * * * *" --endpoint styles
```

`fetch` defaults to delta mode. `--full` refetches all configured endpoints and still advances the
delta state after successful endpoint fetches. `--days` and `--months` run explicit `_modified_at`
windows.

Raw JSONL, checkpoints, logs, `delta.yml`, and the canonical SQLite cache live under
`~/.centric-api`. The local database defaults to `~/.centric-api/centric.db`.
Manual fetches append human-readable run logs to `~/.centric-api/logs/fetch.log` by default. The
default `--log-level summary` writes run, endpoint, ingest, and changelog summary lines. Use
`--log-level http` to include API request/response diagnostics, `--log-level debug` for
checkpoint/resume internals, or `--log-level off` to disable the log.

`cron` runs in the foreground until stopped and defaults to hourly (`0 * * * *`). It prints
scheduler lifecycle messages and concise fetch summaries to the terminal, runs fetch in quiet JSON
mode, and writes JSONL-only records to `~/.centric-api/logs/cron.jsonl`. Fetch runs are serialized
with `~/.centric-api/fetch.lock`.

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
skipped. `--sync` verifies all selected latest revisions exist without overwriting existing files.
`--rebuild` redownloads selected latest revisions and tombstones current download rows that are no
longer selected, while preserving the last known good current revision if a replacement download
fails. Download runs are serialized with
`CENTRIC_API_HOME/download.lock`, and binary downloads retry transient HTTP/server hiccups with a
simple 15s/30s backoff. The default config is `config/download.yml`, with a fuller multi-job example
in `config/download.example.yml`; place `download.yml` in `CENTRIC_API_HOME` for private jobs, or
pass `--download-config`. Files are stored under
`CENTRIC_API_HOME/downloads/files`, each run writes a manifest under
`CENTRIC_API_HOME/downloads/runs`, current download state is tracked in the `download_current` SQLite
table, and human-readable download logs append to `CENTRIC_API_HOME/logs/download.log`.

If `~/.centric-api/delta.yml` does not exist, the first delta fetch starts with no floor, so it
fetches all configured records and writes the delta state after successful endpoint fetches. To seed
the file manually, copy `config/delta.example.yml` to `~/.centric-api/delta.yml`. Delta fetches
default to a 10-minute overlap from the previous successful fetch start.

Credentials resolve from environment variables or `~/.centric-api/local.env`:

```bash
CENTRIC_BASE_URL=https://centric.example.com
CENTRIC_USERNAME=user@example.com
CENTRIC_PASSWORD=secret
```

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

Full fetch ingest is authoritative per successful endpoint. Current local records missing from a
successful full snapshot are removed from `endpoint_records` and written as synthetic hard-delete
tombstones.

For dashboard-style queries, changelog also writes field-level rows and compact rollups:
`endpoint_change_summary`, `endpoint_field_change_summary`, `endpoint_actor_change_summary`, and
`endpoint_actor_field_change_summary`. Use `centric-api changelog fields` and
`centric-api changelog actors` for quick aggregate views.
