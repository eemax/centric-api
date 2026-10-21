# Running centric-api on a server

A recommended setup for keeping a Centric copy up to date unattended: which endpoints to
fetch, a schedule, upkeep, monitoring, and how consumers hook in. Ready-made files are in
[`examples/`](../examples/). Adapt paths and times to your machine.

## Pick your endpoints

Fetch only what your consumers read: the endpoint count drives fetch time, API load and
database size. Start from one of these and edit:

| File | Endpoints | For |
|---|---|---|
| [`examples/endpoints/minimal.yml`](../examples/endpoints/minimal.yml) | 2: seasons, styles | Trying it out; a first look at your instance |
| [`examples/endpoints/dpp-compliance.yml`](../examples/endpoints/dpp-compliance.yml) | 12: styles, seasons, BOMs down to materials, suppliers, factories, lookups, users | Product passport / compliance checks: what a product is made of and where |
| [`examples/endpoints/all.yml`](../examples/endpoints/all.yml) | All 42 bundled | Exploring, or a general-purpose mirror (same as leaving `endpoints:` out) |

Each is a complete config file: copy it to `centric-api.yml` (or point `--config` at it).
Adding an endpoint later is safe: the next fetch picks it up in full. Removing one stops
fetching it; its stored records stay until you delete them.

For scale: the 12-endpoint DPP set on a large instance (~360k records) is about 1 GB on
disk, takes ~45 minutes for the first (full) fetch and about a minute for a daily
incremental one.

## Install

```sh
useradd --system --home-dir /var/lib/centric-api --shell /usr/sbin/nologin centric-api
git clone https://github.com/eemax/centric-api.git /opt/centric-api
cd /opt/centric-api && git checkout <commit>       # pin it; upgrade deliberately
uv sync --frozen --no-dev
install -d -m 755 /etc/centric-api
sed -e 's|^database: .*|database: /var/lib/centric-api/centric.db\nenv_file: /etc/centric-api/centric.env|' \
  examples/endpoints/dpp-compliance.yml > /etc/centric-api/centric-api.yml
```

Put the credentials in `/etc/centric-api/centric.env` (`CENTRIC_BASE_URL`,
`CENTRIC_USERNAME`, `CENTRIC_PASSWORD`), owned by root, group `centric-api`, mode 640.
Nothing else needs them: consumers only read the database.

Run consumers as the same `centric-api` user. They open the database read-only, but
SQLite (WAL mode) still creates `centric.db-shm`/`-wal` next to it when no one else has
it open, so a reader running as another user fails with "attempt to write a readonly
database" unless it can write to `/var/lib/centric-api`.

Pin a commit rather than tracking `main`: a scheduled fetch shouldn't change under you. When
you upgrade, upgrade consumers that use `centric_api.read.Reader` to the same commit, since
the Reader refuses a database with a different schema version.

## Schedule

| Job | When | Why |
|---|---|---|
| `fetch` (incremental) | Daily, e.g. 01:00 (hourly if you need fresher data) | Reads only what changed since the last run (with a 10-min overlap) |
| `fetch --full --allow-delete` | Weekly, **instead of** that day's incremental | Picks up records deleted in Centric, and anything an incremental could miss |
| `maintain --vacuum` | Right after the weekly full fetch | Integrity check, WAL checkpoint, optimize, gives free space back |
| Consumers (reports, exports) | After each **successful** fetch, not on a clock | They never see half-fetched data |
| `check --max-age 26h` | Every minute or so, from your monitoring | Reads only bookkeeping; non-zero = alert |

With systemd, copy [`examples/systemd/`](../examples/systemd/) to `/etc/systemd/system/`
(the units run as the `centric-api` user, with `/var/lib/centric-api` as their state
directory and a hardened sandbox), then:

```sh
systemctl daemon-reload
systemctl start centric-api-fetch          # first run is a full fetch; watch it:
journalctl -fu centric-api-fetch
systemctl enable --now centric-api-fetch.timer centric-api-full.timer
```

Without systemd, [`examples/cron/crontab`](../examples/cron/crontab) does the same with cron.

Points the examples get right, worth keeping if you write your own:

- **Treat only exit 0 as success.** 1 = an endpoint failed, 2 = couldn't start (config,
  credentials, another run holds the lock), 130/143/129 = stopped. Never mark the
  stop statuses as successful, or consumers run on a partial fetch.
- **Bound runs with a timeout** (the examples use 1h incremental, 4h full). A timeout
  stops cleanly and the next run resumes where this one stopped.
- **One fetch at a time.** The lock refuses a second concurrent fetch (exit 2), so give
  the incremental and full fetches different days rather than relying on it. Don't use
  systemd `Conflicts=`: it would stop the running fetch.
- **Keep reboots out of the fetch window.** A reboot mid-fetch is safe (it resumes), but
  wastes the run. If the box reboots for updates, schedule that an hour or more earlier.

## Disk and memory

- Keep free disk of at least **twice the database size**: VACUUM writes a full copy.
  `maintain` skips VACUUM with a note when there isn't room.
- Fetching needs little memory. Consumers should read with
  `Reader(db).records(endpoint, fields=[...])`, naming only the fields they use: parsing
  happens one record at a time and drops the rest, so ~360k records with ten fields each
  fit in a few hundred MB instead of several GB.
- Need to know whether anything changed before doing expensive work? Store
  `Reader.change_seq()` after each consumer run and ask `changes --since <it>` next time.

## Monitoring

- `centric-api check --max-age 26h -q` exits non-zero when the last fetch failed, an
  endpoint is failing or stale, or a lock was abandoned. Wire that into whatever alerts
  you (`--json` gives the reasons). 26h = daily schedule plus slack.
- `journalctl -u centric-api-fetch` shows one logfmt line per endpoint and one per run
  (`event=run status=ok … records_changed=… change_seq=…`), easy to grep or ship.
- `centric-api status` shows per-endpoint counts and last success; safe during a fetch.
