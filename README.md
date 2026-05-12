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
uv run centric-api cron
uv run centric-api cron "0 * * * *" --endpoint styles
```

`fetch` defaults to delta mode. `--full` refetches all configured endpoints and still advances the
delta state after successful endpoint fetches. `--days` and `--months` run explicit `_modified_at`
windows.

Raw JSONL, checkpoints, logs, `delta.yml`, and the canonical SQLite cache live under
`~/.centric-api`. The local database defaults to `~/.centric-api/centric.db`.

`cron` runs in the foreground until stopped and defaults to hourly (`0 * * * *`). It prints
scheduler lifecycle messages and concise fetch summaries to the terminal, runs fetch in quiet JSON
mode, and writes JSONL-only records to `~/.centric-api/logs/cron.log`. Lock files always live under
`~/.centric-api/cron`.

If `~/.centric-api/delta.yml` does not exist, the first delta fetch starts with no floor, so it
fetches all configured records and writes the delta state after successful endpoint fetches. To seed
the file manually, copy `config/delta.example.yml` to `~/.centric-api/delta.yml`.

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
added, changed, and removed events after ingest.

For dashboard-style queries, changelog also writes `endpoint_change_fields`, one row per top-level
field change. Use `centric-api changelog fields` for a quick aggregate view.
