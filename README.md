# centric-api

Fetch data from the [Centric PLM](https://www.centricsoftware.com/) REST API into a local SQLite
database, in two layers:

- **Bronze**: one row per record with the full JSON payload exactly as Centric returned it.
- **Silver**: one typed table per endpoint, with columns and types taken from your Centric
  instance's own API spec (swagger), plus link tables for list fields.

Fetches are incremental by default, resume where they stopped after a crash, and keep all fetch
state in the database itself. There are no intermediate files.

## Install

Requires Python 3.12 or newer.

```sh
pip install git+https://github.com/eemax/centric-api.git
# or, from a checkout
uv sync
```

## Configure

Credentials come from environment variables, or from a `.env` file next to your config file
(the current directory when there is no config file, or the path set as `env_file`):

```sh
CENTRIC_BASE_URL=https://yourcompany.centricsoftware.com
CENTRIC_USERNAME=api-user
CENTRIC_PASSWORD=...
```

`CENTRIC_BASE_URL` also accepts just `yourcompany` or `yourcompany.centricsoftware.com`.
Environment variables take precedence over the `.env` file.

Everything else is optional. Without a config file, centric-api writes `./centric.db` and fetches
the bundled default endpoint list. To customize, create a config file:

```sh
centric-api init            # writes centric-api.yml with comments
```

The config file is found via `--config PATH`, then `$CENTRIC_API_CONFIG`, then
`./centric-api.yml`. Relative paths inside it resolve against the file's directory.

## Use

```sh
centric-api fetch           # incremental fetch of every endpoint, then update silver
centric-api fetch --full    # complete fetch; reports records no longer returned
centric-api fetch --full --allow-delete  # ...and flags confirmed deletions
centric-api fetch -e styles -e colorways
centric-api status          # what is stored, when it was fetched, anything pending
centric-api check --max-age 26h   # exit 0 if the last fetch is good and the data fresh
centric-api changes --since 1234  # what changed since an earlier change sequence number
centric-api maintain --vacuum     # integrity check, WAL checkpoint, optimize, reclaim space
centric-api swagger         # refresh the API spec and update silver tables
centric-api silver --rebuild  # rebuild silver from bronze without calling the API
```

### Running it on a schedule

`fetch --log-format logfmt` (or `json`) replaces the human progress output with one line per
endpoint as it finishes, then one for the run (and `event=retry` lines when Centric has to
be asked again), which reads well in journald and log shippers:

```
event=endpoint run_id=… endpoint=styles status=ok mode=incremental fetched=12 new=0 changed=12 restored=0 deleted=0 missing=0 seconds=3.1
event=run run_id=… status=ok mode=incremental endpoints=12 failed=0 records_changed=12 change_seq=48213 seconds=56.0
```

`check` is the health probe for monitors, cheap enough to run every minute (it reads only
the bookkeeping tables, never the records): it exits 0 when the database exists, the latest
fetch didn't fail (one in progress is fine while its lock is alive), every endpoint has a
successful fetch and its last attempt didn't fail, and, with `--max-age`, every endpoint's
last success is recent enough. Otherwise it exits 1 and lists the problems (`--json` for
machines, `-q` for the exit status only). An abandoned lock is a problem too.

`changes` tells consumers whether there's anything new. Every inserted, updated, restored or
deleted record gets a new, higher change sequence number; records a fetch saw unchanged keep
theirs. A consumer stores the `change_seq` it last processed (from `changes`, the fetch
summary or `Reader.change_seq()`), and next time asks `changes --since <it>`: a total of 0
means there's nothing to redo.

`maintain` is weekly-ish upkeep: a quick integrity check (it fails, status 2, if that
finds damage), a WAL checkpoint, `PRAGMA optimize`, and with `--vacuum` a VACUUM that hands
free pages back to the filesystem. It takes the writer lock, so it never overlaps a fetch.
VACUUM writes a full temporary copy and then the new contents through the WAL, so it needs
about twice the database's size in free disk space; it's skipped with a note when that
isn't available.

Add `--json` for a machine-readable summary on stdout, or `-q` to silence progress output.
Exit status:

| Status | Meaning |
|---|---|
| 0 | Done: every endpoint fetched |
| 1 | `fetch` finished, but an endpoint failed or the login was rejected |
| 2 | Couldn't start: invalid configuration, missing credentials, another run holding the lock, or a database error |
| 130 / 143 / 129 | Stopped by SIGINT (Ctrl-C) / SIGTERM / SIGHUP |

`status` only reads the database, so it is safe to run while a fetch is in progress.

**Stopping a run.** Ctrl-C, SIGTERM (what systemd, `kill` and a shutdown send) and SIGHUP (a
closed terminal) all stop cleanly: the page being written is rolled back, the lock is
released, and the next `fetch` resumes where this one stopped. A stopped run never exits 0, so
anything that runs "after a successful fetch" won't run on partial data. A second signal while
it stops is ignored; SIGKILL still kills it outright, and the lock it leaves is reclaimed as
described below.

Schedule `centric-api fetch` with whatever your platform provides (cron, systemd timers, Task
Scheduler, a container job). Treat any non-zero status as "not done"; in particular don't
declare 130/143/129 successful, or follow-up jobs would run on a partial fetch. Run `centric-api fetch --full --allow-delete` periodically, for
example weekly, to pick up deleted records.

## How fetching works

Each run:

1. **Refreshes the API spec** for every API version in use (`/api/v2/swagger.json`,
   `/api/v3/swagger.json`). Centric publishes a separate, smaller spec for v3 endpoints such as
   BOM revisions and part materials, and their payloads differ from v2, so each endpoint is
   typed from the spec of its own API version. The latest spec is stored as one row per version;
   an unchanged spec is not rewritten. If a refresh fails, the stored spec is used; if a
   version's spec has never been fetched, its endpoints use the v2 spec and the summary says so.
2. **Fetches each endpoint** in pages, asking Centric to sort by `_modified_at`:
   - **Incremental** (default): records modified since the last successful fetch started, minus
     `delta.overlap_minutes` (10 by default). An endpoint that has never been fetched gets a
     full fetch.
   - **Full** (`--full`): every record.
3. **Updates silver** for the records that changed.

Every page is written to bronze in its own transaction together with the fetch position. If a
run is interrupted, the next `fetch` resumes each unfinished endpoint where it stopped, including
an unfinished full fetch when the next run is a plain `fetch` (`--restart` starts over instead).

Pages are read until Centric returns an empty page, advancing by the number of records actually
received, so a server that returns fewer records than asked for never causes gaps. Each finished
window is then either **proven** complete or not:

- **Proven** when every record arrived once and at least Centric's count was fetched. Records
  created while paging can push the total above the count; that is fine. For incremental
  windows there is one more condition: no record may have changed while the window was being
  read. A record deleted mid-fetch makes the pages skip one record, and a record created at the
  same time would hide that in the totals, so a window that saw a change is checked by a second
  pass. "Changed" counts from the window start less `overlap_minutes`, so a Centric clock that
  runs behind cannot hide a change.
- Otherwise a record may have been skipped: Centric's sort is not strict, so a record that is
  edited, created or deleted mid-fetch can shift the pages. An **incremental** window is then
  read again, up to three passes in total. A pass proves the window complete when it finds no
  record the earlier passes missed, or is itself distinct and complete. Passes that agree also
  settle a count Centric reports but cannot list (records it counts but never returns).
- A **full** window is not re-read (except on an endpoint's very first fetch). Its missing
  records are looked up by id instead, see below. If records changed while it was being read,
  the incremental cursor simply stays where it was, so the next incremental run re-reads
  everything changed since then.

A window that cannot be proven keeps all its data, but the incremental cursor stays where it was,
so the next run reads the same span again. Nothing is ever accepted on a "close enough" count.
An endpoint only fails on HTTP errors, invalid data (such as a record without an `id`), or a
server that misbehaves (for example ignores `skip`); the other endpoints still run.

Only one run can write to a database at a time; a second run exits with an error. The lock
records which host and process holds it. If that process is gone on this same host (it
crashed, or was killed with SIGKILL), the next run takes the lock over immediately. Otherwise
(a lock held from another machine, or by a process that still exists) it is only treated as
abandoned once its heartbeat is 15 minutes old.

### Deleted records

Centric's API does not report deletions, so incremental fetches never flag anything. After a
**full** fetch, every stored record it did not return is looked up by id (50 per request,
without the endpoint's `query_params`):

- Records Centric still returns were skipped while paging or fall outside the endpoint's
  filters. They are stored, with a warning.
- Records Centric no longer has are reported as `missing`. They are only flagged
  (`deleted = 1`) when you also pass `--allow-delete`:

```sh
centric-api fetch --full --allow-delete
```

Nothing is ever flagged when a full fetch returns no records at all, or when an endpoint does
not support looking records up by id (the endpoint fails instead of guessing).

Deleted records stay in both layers with the flag set, and a record that reappears is restored.
Records whose `active` field is `false` are inactive, not deleted; they stay as normal rows.

Records are identified by endpoint `name`. If you point an existing name at a different `path`,
records from the old path will be flagged deleted by the next `--full --allow-delete`.

## Database layout

All tables live in one SQLite file. Tables starting with `meta_`, `bronze_` and `silver_` are
managed by centric-api; do not write to them. centric-api refuses to use an existing SQLite file
that already holds other tables.

The database uses SQLite's WAL mode, so readers (BI tools, scripts, `status`) never block a
running fetch. Keep it on a local disk: WAL does not work on network file shares.

### Reading it from your own code

`centric_api.read.Reader` is the supported way for other programs to consume the database.
It opens it read-only, checks the schema version, and keeps working when the internal table
layout changes:

```python
from centric_api.read import Reader

with Reader("centric.db") as db:
    for line in db.records("bom_lines", fields=["ds_section", "actual", "qty_default"]):
        ...  # {"id": ..., "ds_section": ..., ...}
    materials = db.records_by_id("materials", fields=["node_name", "product_type"])
    seq = db.change_seq()  # store this with your output
    if not any(c.total for c in db.changes(since=previous_seq, endpoints=["styles"])):
        ...  # nothing you read has changed
```

Records are the payloads exactly as Centric returned them. With `fields`, each payload is
parsed ([orjson](https://github.com/ijl/orjson), falling back to the standard library for
the odd payload it rejects) and only those top-level fields are kept; the rest is freed
straight away. On a 364k-record database, reading every record with the fields one
validator needs takes about 1 s while streaming, and holding them all takes 0.35 GB
instead of 3.1 GB for full payloads. `id` comes first, then the fields in the order asked;
a field missing from a payload is missing from the dict (so `"x" in record` still means
something), and a JSON `null` stays `None`. Deleted records are left out unless
`include_deleted=True`. Records stream in `id` order.

### Bronze

`bronze_records` has one row per `(endpoint, id)`:

| Column | Meaning |
|---|---|
| `payload` | The record's JSON exactly as returned by Centric |
| `modified_at` | The record's `_modified_at` |
| `deleted`, `deleted_at` | Set by `fetch --full --allow-delete` once Centric confirms the record is gone |
| `first_seen_at`, `updated_at`, `last_seen_at` | When the record was first stored, last changed, last returned |

A stored record is only replaced by a payload with the same or a newer `_modified_at`, so
overlapping fetches never roll a record back. There is no history: each record holds its latest
version.

### Silver

For each endpoint, `silver_<endpoint>` (for example `silver_styles`) has:

- `id` as the primary key,
- `_row_deleted`, `_row_deleted_at` and `_row_updated_at` mirroring bronze,
- one column per field in the endpoint's response schema in the API spec.

Column types come from the spec:

| Spec type | Column type |
|---|---|
| `string` | `TEXT` |
| `string` with `format: date-time` | `TEXT` (ISO 8601) |
| `integer` | `INTEGER` |
| `number` | `REAL` |
| `boolean` | `INTEGER` (0 or 1) |
| object without a fixed shape | `TEXT` (JSON) |

List fields (for example a style's `product_colors`) and keyed map fields (Centric sometimes
returns lists as `{"0": ..., "1": ...}`) become link tables named
`silver_<endpoint>__<field>` with columns `id`, `position`, `key` and `value`. References hold
the target record's full id, for example `C0/1000|Colorway`, so they join directly on the target
table's `id`. The `meta_silver_fields` table lists every field with its spec type and, where the
spec says so, the object type it references.

Silver follows these rules:

- **Fields come from the spec only.** A payload field the spec does not describe stays in bronze
  and is listed as unmapped (in `status`, the fetch summary and `meta_unmapped_fields`). When the
  spec later describes it, the next run adds it to silver and fills it for every record.
- **Columns are never dropped.** A field that leaves the spec keeps its column, which is then
  empty for new data.
- **Types only widen.** If the spec changes a field's type, or the data contradicts the spec,
  the column becomes wider (`INTEGER` to `REAL`, anything else to `TEXT`) and is never narrowed.
- Any change to an endpoint's columns rebuilds that endpoint's silver tables from bronze in a
  single transaction, so readers never see a half-built table.

Silver is derived entirely from bronze and the stored spec. `centric-api silver --rebuild`
recreates it at any time without calling the API.

### Fetch state

| Table | Contents |
|---|---|
| `meta_endpoint_state` | Incremental cursor (also shown by `status --json`) and any unfinished fetch per endpoint |
| `meta_runs`, `meta_run_endpoints` | One row per run and per endpoint in each run |
| `meta_swagger` | Latest API spec per version |
| `meta_silver_fields`, `meta_silver_state` | Silver column registry and sync position |
| `meta_unmapped_fields` | Payload fields not described by the spec |
| `meta_lock`, `meta_info` | The single-writer lock, and the schema version and change counter |

## Endpoints

The bundled list covers common Centric objects (styles, colorways, materials, BOMs and BOM
lines, suppliers, sizes, documents and more). Not every Centric instance has every object type;
an endpoint your instance lacks fails with a message saying so. To change the list, set
`endpoints:` in your config; it replaces the whole list. Entries can name bundled endpoints,
optionally overriding some keys, or define new ones in full:

```yaml
endpoints:
  - styles                        # the bundled definition as is
  - name: bom_lines               # same
  - name: boms                    # bundled, with a different page size
    limit: 200
  - name: my_objects              # not bundled: give path and count_path
    path: my_objects
    count_path: count/MyObject
```

Page size matters: a full fetch is bound by request count, not data volume, so the bundled
definitions use `limit: 500` for the high-volume endpoints where that's been measured to work
(about 10x the records for 2x the time per request); endpoints with heavy payloads, such as
styles, stay at 50. A full definition takes:

| Key | Meaning |
|---|---|
| `name` | Local name, lowercase snake_case; also the silver table suffix |
| `path` | API path, for example `part_materials` |
| `api_version` | `v2` or `v3` |
| `count_path` | Count endpoint, for example `count/PartMaterial` |
| `limit` | Page size (default 50) |
| `query_params` | Optional extra filters; `skip`, `limit`, `sort` and `_modified_at` are managed |

## Development

```sh
uv sync
just check    # format check, lint, smoke test, tests
```

## License

MIT
