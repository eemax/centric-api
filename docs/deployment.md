# Deployment

This guide shows a simple Linux `systemd` setup for unattended fetches.

The recommended production shape is a timer-triggered one-shot service:

```text
centric-api-fetch.timer -> centric-api-fetch.service -> centric-api fetch
```

The service runs one fetch and exits. It is not a daemon. Fetch locking still prevents overlapping
runs if a previous fetch is active.

## Paths

The example units use these paths:

| Path | Purpose |
| --- | --- |
| `/opt/centric-api` | application checkout |
| `/opt/centric-api/.venv/bin/centric-api` | installed CLI |
| `/etc/centric-api/centric-api.env` | credentials and runtime environment |
| `/var/lib/centric-api` | `CENTRIC_API_HOME` runtime state |
| `/etc/systemd/system/centric-api-fetch.service` | systemd service |
| `/etc/systemd/system/centric-api-fetch.timer` | systemd timer |

## Install

Create a service user and runtime directories:

```bash
sudo useradd --system --home /var/lib/centric-api --shell /usr/sbin/nologin centric-api
sudo install -d -o centric-api -g centric-api -m 0750 /var/lib/centric-api
sudo install -d -o root -g centric-api -m 0750 /etc/centric-api
```

Install the app under `/opt/centric-api` and create the virtual environment:

```bash
sudo git clone https://github.com/eemax/centric-api.git /opt/centric-api
sudo chown -R centric-api:centric-api /opt/centric-api
cd /opt/centric-api
sudo -u centric-api uv sync
```

This assumes `uv` is installed on the host. If the checkout already exists, update it in place and
rerun `uv sync` as the `centric-api` user.

Install the environment file and edit credentials:

```bash
sudo cp /opt/centric-api/deploy/systemd/centric-api.env.example /etc/centric-api/centric-api.env
sudo chown root:centric-api /etc/centric-api/centric-api.env
sudo chmod 0640 /etc/centric-api/centric-api.env
sudo editor /etc/centric-api/centric-api.env
```

Install and enable the systemd units:

```bash
sudo cp /opt/centric-api/deploy/systemd/centric-api-fetch.service /etc/systemd/system/
sudo cp /opt/centric-api/deploy/systemd/centric-api-fetch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now centric-api-fetch.timer
```

## Operate

Run a fetch immediately:

```bash
sudo systemctl start centric-api-fetch.service
```

Inspect timer state:

```bash
systemctl list-timers centric-api-fetch.timer
```

Inspect the latest service result:

```bash
sudo systemctl status centric-api-fetch.service
```

Read journald output:

```bash
sudo journalctl -u centric-api-fetch.service -n 100 --no-pager
```

Follow the app's durable fetch log:

```bash
sudo tail -f /var/lib/centric-api/logs/fetch.log
```

## Customize

Change the timer schedule by editing `OnCalendar` in
`/etc/systemd/system/centric-api-fetch.timer`, then reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart centric-api-fetch.timer
```

For example, every 15 minutes:

```ini
OnCalendar=*:0/15
```

To fetch only selected endpoints, edit `ExecStart` in
`/etc/systemd/system/centric-api-fetch.service`:

```ini
ExecStart=/opt/centric-api/.venv/bin/centric-api fetch --endpoint styles --endpoint colorways
```

Reload systemd after unit edits:

```bash
sudo systemctl daemon-reload
```

Use `centric-api doctor` after config changes:

```bash
sudo -u centric-api env CENTRIC_API_HOME=/var/lib/centric-api \
  /opt/centric-api/.venv/bin/centric-api doctor \
  --env-file /etc/centric-api/centric-api.env
```
