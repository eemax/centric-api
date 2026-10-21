"""Operations commands: check, changes, maintain, structured logs, endpoints by name."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest

from centric_api import cli, maintenance
from centric_api import fetch as fetch_module
from centric_api.config import ConfigError, load_settings
from centric_api.db import LockError, RunLock, connect, iso, transaction, utc_now
from centric_api.fetch import run_fetch
from centric_api.maintenance import collect_health, parse_duration, run_maintain
from tests.fake_centric import FakeCentric, make_settings, ts
from tests.test_cli import workspace  # noqa: F401  (fixture)


def _seeded(tmp_path):
    fake = FakeCentric()
    for n in range(1, 4):
        fake.add("styles", id=f"S{n}", _modified_at=ts(n), node_name=f"Style {n}")
    fake.add("colorways", id="C1", _modified_at=ts(1), node_name="Black")
    settings = make_settings(tmp_path)
    return fake, settings


# ----------------------------------------------------------------------- check


def test_check_healthy_after_a_good_fetch(tmp_path) -> None:
    fake, settings = _seeded(tmp_path)
    run_fetch(settings, client=fake.client())
    health = collect_health(settings, max_age=timedelta(hours=1))
    assert health["healthy"], health["problems"]
    assert {e["endpoint"] for e in health["endpoints"]} == {"styles", "colorways"}
    assert health["last_fetch"]["status"] == "ok"


def test_check_flags_missing_db_never_fetched_stale_and_failed(tmp_path) -> None:
    fake, settings = _seeded(tmp_path)
    assert collect_health(settings)["problems"] == [
        f"database not created yet: {settings.database}"
    ]

    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    health = collect_health(settings)
    assert health["problems"] == ["colorways: never fetched successfully"]

    run_fetch(settings, client=fake.client())
    later = utc_now() + timedelta(hours=30)
    stale = collect_health(settings, max_age=timedelta(hours=26), now=later)
    assert not stale["healthy"]
    assert all("older than 26h" in p for p in stale["problems"]) and len(stale["problems"]) == 2
    assert collect_health(settings, endpoint_names=["styles"], now=later)["healthy"]

    conn = sqlite3.connect(settings.database)
    conn.execute(
        "UPDATE meta_runs SET status = 'partial', error = 'boom' WHERE started_at = "
        "(SELECT MAX(started_at) FROM meta_runs)"
    )
    conn.commit()
    assert collect_health(settings)["problems"][0].startswith("last fetch partial")


def test_check_running_fetch_is_fine_but_an_abandoned_one_is_not(tmp_path) -> None:
    fake, settings = _seeded(tmp_path)
    run_fetch(settings, client=fake.client())
    conn = connect(settings.database)
    lock = RunLock(conn)
    lock.acquire()  # a fetch in progress, from this host
    with transaction(conn):
        conn.execute(
            "INSERT INTO meta_runs (run_id, command, started_at, status) VALUES "
            "('r2', 'fetch', ?, 'running')",
            [iso(utc_now())],
        )
    running = collect_health(settings)
    assert running["healthy"] and running["running"]

    import socket
    import subprocess
    import sys

    dead = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    with transaction(conn):  # fresh heartbeat, but its process is gone (crashed)
        conn.execute("UPDATE meta_lock SET owner = ?", [f"{socket.gethostname()}:{dead}:x"])
    crashed = collect_health(settings)
    assert not crashed["healthy"] and not crashed["running"]

    with transaction(conn):
        conn.execute("UPDATE meta_lock SET heartbeat_at = ?", [iso(utc_now() - timedelta(hours=1))])
    abandoned = collect_health(settings)
    assert not abandoned["healthy"]
    assert any("abandoned lock" in p for p in abandoned["problems"])
    assert any("never finished" in p for p in abandoned["problems"])


@pytest.mark.parametrize(
    ("text", "seconds"), [("90", 90), ("45m", 2700), ("26h", 93600), ("2d", 172800), ("1.5h", 5400)]
)
def test_parse_duration(text, seconds) -> None:
    assert parse_duration(text).total_seconds() == seconds


@pytest.mark.parametrize("text", ["", "h", "-5m", "0", "5w"])
def test_parse_duration_rejects(text) -> None:
    with pytest.raises(ConfigError):
        parse_duration(text)


def test_check_cli_exit_codes_and_json(workspace, capsys) -> None:  # noqa: F811
    assert cli.main(["check", "-q"]) == 1  # nothing fetched yet
    assert cli.main(["fetch", "-q"]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--max-age", "1h"]) == 0
    assert capsys.readouterr().out.startswith("healthy")
    assert cli.main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True
    assert cli.main(["check", "--max-age", "nonsense"]) == 2


# --------------------------------------------------------------------- changes


def test_changes_cli_and_fetch_summary_report_what_moved(workspace, capsys) -> None:  # noqa: F811
    assert cli.main(["fetch", "--json", "-q"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["records_changed"] == 1 and first["change_seq"]["after"] > 0
    seq = first["change_seq"]["after"]

    assert cli.main(["fetch", "--full", "--json", "-q"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["records_changed"] == 0 and again["change_seq"]["after"] == seq

    assert cli.main(["changes", "--since", str(seq), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 0 and payload["change_seq"] == seq
    assert cli.main(["changes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["endpoints"][0]["changed"] == 1


# ------------------------------------------------------------------ log format


@pytest.mark.parametrize("fmt", ["logfmt", "json"])
def test_structured_log_lines_per_endpoint_and_run(workspace, capsys, fmt) -> None:  # noqa: F811
    assert cli.main(["fetch", "--log-format", fmt]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 2  # one endpoint (styles) + the run
    if fmt == "json":
        endpoint, run = (json.loads(line) for line in lines)
    else:
        endpoint, run = (dict(part.split("=", 1) for part in line.split()) for line in lines)
    assert endpoint["event"] == "endpoint" and endpoint["endpoint"] == "styles"
    assert str(endpoint["new"]) == "1" and str(endpoint["status"]) == "ok"
    assert run["event"] == "run" and str(run["records_changed"]) == "1"
    assert str(run["status"]) == "ok" and str(run["mode"]) == "incremental"


def test_logfmt_quotes_values_that_need_it() -> None:
    assert cli._logfmt("plain") == "plain"
    assert cli._logfmt("two words") == '"two words"'
    assert cli._logfmt('a="b"') == '"a=\\"b\\""'
    assert cli._logfmt("") == '""'
    assert cli._logfmt(True) == "true"


# ------------------------------------------------------------ endpoints by name


def _config(tmp_path, body: str):
    path = tmp_path / "centric-api.yml"
    path.write_text(body, encoding="utf-8")
    return load_settings(path)


def test_endpoints_by_bundled_name_with_overrides(tmp_path) -> None:
    settings = _config(
        tmp_path,
        """
endpoints:
  - styles
  - name: bom_lines
  - name: boms
    limit: 7
  - name: mine
    path: my_things
    count_path: count/MyThing
""",
    )
    by = {spec.name: spec for spec in settings.endpoints}
    assert [spec.name for spec in settings.endpoints] == ["styles", "bom_lines", "boms", "mine"]
    assert (by["styles"].path, by["styles"].limit) == ("styles", 50)
    assert (by["bom_lines"].path, by["bom_lines"].api_version, by["bom_lines"].limit) == (
        "part_materials",
        "v3",
        500,
    )
    assert (by["boms"].path, by["boms"].limit) == ("apparel_bom_revisions", 7)
    assert by["mine"].path == "my_things"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("endpoints:\n  - not_bundled\n", "not a bundled endpoint"),
        ("endpoints:\n  - {name: nope, limit: 5}\n", "not a bundled endpoint"),
        ("endpoints:\n  - styles\n  - styles\n", "Duplicate endpoint"),
        ("endpoints:\n  - {name: styles, colour: red}\n", "unknown keys"),
    ],
)
def test_endpoint_reference_errors(tmp_path, body, message) -> None:
    with pytest.raises(ConfigError, match=message):
        _config(tmp_path, body)


# --------------------------------------------------------------------- maintain


def test_maintain_checks_checkpoints_and_vacuums(tmp_path) -> None:
    fake = FakeCentric()
    for n in range(300):
        fake.add("styles", id=f"S{n:03}", _modified_at=ts(1), node_name="x" * 2000)
    settings = make_settings(tmp_path, limit=100)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = sqlite3.connect(settings.database)
    conn.execute("DELETE FROM bronze_records")  # leave free pages behind
    conn.execute("DELETE FROM silver_styles")
    conn.commit()
    conn.close()

    plain = run_maintain(settings)
    assert plain.integrity == "ok" and not plain.vacuumed and plain.wal_after == 0

    result = run_maintain(settings, vacuum=True)
    assert result.vacuumed and result.size_after < result.size_before


def test_maintain_skips_vacuum_without_disk_space(tmp_path, monkeypatch) -> None:
    fake, settings = _seeded(tmp_path)
    run_fetch(settings, client=fake.client())

    class Usage:
        free = 1

    monkeypatch.setattr(maintenance.shutil, "disk_usage", lambda _path: Usage)
    result = run_maintain(settings, vacuum=True)
    assert not result.vacuumed and "vacuum skipped" in result.notes[0]


def test_maintain_waits_for_no_fetch(tmp_path) -> None:
    fake, settings = _seeded(tmp_path)
    run_fetch(settings, client=fake.client())
    holder = RunLock(connect(settings.database))
    holder.acquire()
    with pytest.raises(LockError):
        run_maintain(settings)


def test_maintain_cli(workspace, capsys) -> None:  # noqa: F811
    cli.main(["fetch", "-q"])
    capsys.readouterr()
    assert cli.main(["maintain", "--vacuum", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["integrity"] == "ok" and payload["vacuumed"] is True


# ------------------------------------------------------------- review follow-ups


def test_retries_show_up_as_events(tmp_path, monkeypatch) -> None:
    """With structured logs, HTTP retries (only text progress before) are events too."""
    fake, settings = _seeded(tmp_path)
    for key, value in (("BASE_URL", "acme"), ("USERNAME", "user"), ("PASSWORD", "secret")):
        monkeypatch.setenv(f"CENTRIC_{key}", value)
    real_client = fetch_module.CentricClient

    def client_factory(credentials, http, **kwargs):
        # run_fetch builds its own client (and wires on_retry); point it at the fake.
        return real_client(
            credentials, http, transport=fake.transport(), sleep=lambda _s: None, **kwargs
        )

    monkeypatch.setattr(fetch_module, "CentricClient", client_factory)
    fake.status_queue = [503]
    events: list[dict] = []
    run_fetch(settings, on_event=events.append)

    retries = [e for e in events if e["event"] == "retry"]
    assert len(retries) == 1 and "503" in retries[0]["message"]
    assert events[-1]["event"] == "run" and events[-1]["status"] == "ok"


def test_changes_rejects_unknown_endpoints(workspace, capsys) -> None:  # noqa: F811
    cli.main(["fetch", "-q"])
    capsys.readouterr()
    assert cli.main(["changes", "-e", "nope"]) == 2
    assert "Unknown endpoint" in capsys.readouterr().err


def test_logfmt_quotes_whitespace_and_control_characters() -> None:
    assert cli._logfmt("line one\nline two") == '"line one\\nline two"'
    assert cli._logfmt("tab\there") == '"tab\\there"'


def test_stored_endpoints_and_latest_change_seq(tmp_path) -> None:
    from centric_api.db import latest_change_seq, stored_endpoints

    fake, settings = _seeded(tmp_path)
    conn = connect(settings.database)
    assert stored_endpoints(conn) == [] and latest_change_seq(conn) == 0
    run_fetch(settings, client=fake.client())
    assert stored_endpoints(conn) == ["colorways", "styles"]
    seqs = [row[0] for row in conn.execute("SELECT change_seq FROM bronze_records")]
    assert latest_change_seq(conn) == max(seqs)
    styles_max = conn.execute(
        "SELECT MAX(change_seq) FROM bronze_records WHERE endpoint = 'styles'"
    ).fetchone()[0]
    assert latest_change_seq(conn, ["styles"]) == styles_max
