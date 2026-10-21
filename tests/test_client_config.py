from __future__ import annotations

from datetime import timedelta

import pytest

from centric_api.client import AuthError, FetchError, load_credentials, normalize_base_url
from centric_api.client import query_params as compile_params
from centric_api.config import ConfigError, default_endpoints, load_settings
from centric_api.db import (
    DatabaseError,
    LockError,
    RunLock,
    connect,
    iso,
    transaction,
    utc_now,
)
from centric_api.swagger import refresh_specs
from tests.fake_centric import FakeCentric


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme", "https://acme.centricsoftware.com/csi-requesthandler"),
        ("acme.centricsoftware.com", "https://acme.centricsoftware.com/csi-requesthandler"),
        (
            "https://acme.centricsoftware.com/",
            "https://acme.centricsoftware.com/csi-requesthandler",
        ),
        (
            "https://acme.centricsoftware.com/csi-requesthandler",
            "https://acme.centricsoftware.com/csi-requesthandler",
        ),
        ("https://plm.example.com/custom", "https://plm.example.com/custom"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


def test_credentials_env_overrides_env_file(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport CENTRIC_BASE_URL=acme\nCENTRIC_USERNAME='file-user'\n"
        'CENTRIC_PASSWORD="file pass"\n',
        encoding="utf-8",
    )
    for key in ("CENTRIC_BASE_URL", "CENTRIC_USERNAME", "CENTRIC_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CENTRIC_USERNAME", "env-user")

    credentials = load_credentials(env_file)

    assert credentials.base_url == "https://acme.centricsoftware.com/csi-requesthandler"
    assert credentials.username == "env-user"
    assert credentials.password == "file pass"


def test_missing_credentials_are_reported(tmp_path, monkeypatch) -> None:
    for key in ("CENTRIC_BASE_URL", "CENTRIC_USERNAME", "CENTRIC_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(AuthError, match="CENTRIC_USERNAME, CENTRIC_PASSWORD"):
        load_credentials(_write(tmp_path / ".env", "CENTRIC_BASE_URL=acme\n"))


def test_query_params_compile_operators_and_add_decoded() -> None:
    assert compile_params({"active=": True, "qty=ge": 2, "state": "x"}) == [
        ("active", "true"),
        ("qty", "ge2"),
        ("state", "x"),
        ("decoded", "true"),
    ]
    assert compile_params({"decoded": False}) == [("decoded", "false")]


def test_client_retries_transient_errors_and_relogs_in_after_401() -> None:
    fake = FakeCentric()
    fake.status_queue = [503, 429]
    client = fake.client()

    assert client.get_json(client.url("v2", "count/Style")) == {"count": 0}
    assert client.retries == 2

    fake.expire_token_once = True
    assert client.get_json(client.url("v2", "count/Style")) == {"count": 0}
    assert fake.logins == 2


def test_client_honours_retry_after_and_retries_login() -> None:
    fake = FakeCentric()
    sleeps: list[float] = []
    client = fake.client()
    client._sleep = sleeps.append
    fake.login_status_queue = [503]
    fake.status_queue = [429]
    fake.retry_after = "7"

    assert client.get_json(client.url("v2", "count/Style")) == {"count": 0}
    assert sleeps == [0.01, 7.0]

    fake.login_status_queue = [401]
    client._token = None
    with pytest.raises(AuthError, match="HTTP 401"):
        client.get_json(client.url("v2", "count/Style"))


def test_client_gives_up_after_max_attempts_and_on_client_errors() -> None:
    fake = FakeCentric()
    fake.status_queue = [503, 503, 503]
    client = fake.client()
    with pytest.raises(FetchError, match="after 3 attempts"):
        client.get_json(client.url("v2", "count/Style"))
    with pytest.raises(FetchError, match="HTTP 404"):
        client.get_json(client.url("v2", "unknown"))


def test_settings_defaults_without_config_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CENTRIC_API_CONFIG", raising=False)

    settings = load_settings()

    assert settings.database == tmp_path / "centric.db"
    assert settings.config_file is None
    assert settings.overlap_minutes == 10
    assert len(settings.endpoints) == len(default_endpoints()) == 42
    bom_lines = next(spec for spec in settings.endpoints if spec.name == "bom_lines")
    assert (bom_lines.path, bom_lines.api_version) == ("part_materials", "v3")


def test_settings_from_file_resolve_relative_paths(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path / "conf" / "centric-api.yml",
        """
database: data/plm.db
env_file: secrets.env
delta:
  overlap_minutes: 30
endpoints:
  - name: styles
    path: styles
    count_path: count/Style
    limit: 100
    query_params:
      "active=": true
""",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings(config)

    assert settings.database == tmp_path / "conf" / "data" / "plm.db"
    assert settings.env_file == tmp_path / "conf" / "secrets.env"
    assert settings.overlap_minutes == 30
    (styles,) = settings.endpoints
    assert (styles.api_version, styles.limit, styles.query_params) == ("v2", 100, {"active=": True})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("datbase: x.db\n", "unknown keys: datbase"),
        ("endpoints: []\n", "non-empty list"),
        ("endpoints:\n  - {name: Styles, path: s, count_path: c}\n", "snake_case"),
        ("endpoints:\n  - {name: a__b, path: s, count_path: c}\n", "snake_case"),
        (
            "endpoints:\n  - {name: s, path: s, count_path: c}\n"
            "  - {name: s, path: t, count_path: c}\n",
            "Duplicate endpoint",
        ),
        ("endpoints:\n  - {name: s, path: s, count_path: c, api_version: v9}\n", "api_version"),
        (
            "endpoints:\n  - {name: s, path: s, count_path: c, query_params: {sort: x}}\n",
            "manages those",
        ),
        ("http:\n  retry_base_seconds: 10\n  retry_max_seconds: 5\n", "retry_max_seconds"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(_write(tmp_path / "centric-api.yml", body))


def test_run_lock_blocks_second_writer_and_expires(tmp_path) -> None:
    conn = connect(tmp_path / "centric.db")
    other = connect(tmp_path / "centric.db")
    first = RunLock(conn)
    first.acquire()
    with pytest.raises(LockError, match="holds the database lock"):
        RunLock(other).acquire()

    stale = iso(utc_now() - timedelta(minutes=16))
    with transaction(conn):
        conn.execute("UPDATE meta_lock SET heartbeat_at = ?", [stale])
    second = RunLock(other)
    second.acquire()
    with pytest.raises(LockError, match="taken over"), transaction(conn):
        first.heartbeat()
    first.release()  # releasing a lock you no longer own leaves the new owner alone
    assert other.execute("SELECT owner FROM meta_lock").fetchone()[0] == second.owner


def test_foreign_database_is_refused(tmp_path) -> None:
    path = tmp_path / "other.db"
    import sqlite3

    other = sqlite3.connect(path)
    other.execute("CREATE TABLE endpoint_records (x)")
    other.commit()
    with pytest.raises(DatabaseError, match="not a centric-api database"):
        connect(path)


def test_readonly_connection_does_not_wait_for_a_writer(tmp_path) -> None:
    path = tmp_path / "centric.db"
    writer = connect(path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE meta_info SET value = value WHERE key = 'change_seq'")

    reader = connect(path, readonly=True)

    assert reader.execute("SELECT COUNT(*) FROM bronze_records").fetchone()[0] == 0
    with pytest.raises(Exception, match="readonly"):
        reader.execute("DELETE FROM meta_info")
    writer.execute("ROLLBACK")


def test_unchanged_swagger_is_not_rewritten(tmp_path) -> None:
    fake = FakeCentric()
    conn = connect(tmp_path / "centric.db")
    client = fake.client()

    assert [r.status for r in refresh_specs(conn, client, {"v2", "v3"})] == ["updated", "failed"]
    assert [r.status for r in refresh_specs(conn, client, {"v2"})] == ["unchanged"]
    fake.specs["v2"]["info"] = {"version": "2"}
    assert [r.status for r in refresh_specs(conn, client, {"v2"})] == ["updated"]
    assert conn.execute("SELECT COUNT(*) FROM meta_swagger").fetchone()[0] == 1


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _lock_owned_by(conn, owner: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO meta_lock (id, owner, acquired_at, heartbeat_at) VALUES (1, ?, ?, ?)",
            [owner, iso(utc_now()), iso(utc_now())],
        )


def test_run_lock_of_a_dead_local_process_is_reclaimed_at_once(tmp_path) -> None:
    import socket
    import subprocess
    import sys

    finished = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(finished.stdout)
    conn = connect(tmp_path / "centric.db")
    _lock_owned_by(conn, f"{socket.gethostname()}:{dead_pid}:deadbeef")  # fresh heartbeat
    lock = RunLock(connect(tmp_path / "centric.db"))
    lock.acquire()
    assert conn.execute("SELECT owner FROM meta_lock").fetchone()[0] == lock.owner


@pytest.mark.parametrize("who", ["live-local", "other-host", "garbled"])
def test_run_lock_of_a_live_or_unknown_owner_is_respected(tmp_path, who) -> None:
    import os
    import socket

    owner = {
        "live-local": f"{socket.gethostname()}:{os.getppid()}:cafe",
        "other-host": f"not-{socket.gethostname()}:1:cafe",
        "garbled": "no-pid-here",
    }[who]
    conn = connect(tmp_path / "centric.db")
    _lock_owned_by(conn, owner)
    with pytest.raises(LockError, match="holds the database lock"):
        RunLock(connect(tmp_path / "centric.db")).acquire()
