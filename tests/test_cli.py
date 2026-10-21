from __future__ import annotations

import json
import os
import signal

import httpx
import pytest

from centric_api import cli
from centric_api.client import CentricClient
from tests.fake_centric import FakeCentric, ts

CONFIG = """
database: centric.db
endpoints:
  - name: styles
    path: styles
    count_path: count/Style
    limit: 2
"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "centric-api.yml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".env").write_text(
        "CENTRIC_BASE_URL=acme\nCENTRIC_USERNAME=user\nCENTRIC_PASSWORD=secret\n",
        encoding="utf-8",
    )
    for key in ("CENTRIC_BASE_URL", "CENTRIC_USERNAME", "CENTRIC_PASSWORD", "CENTRIC_API_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    fake = FakeCentric()
    fake.add("styles", id="S1", _modified_at=ts(1), node_name="Jacket", extra_field=1)
    original = cli.run_fetch.__globals__["CentricClient"]

    def client_factory(credentials, http, **kwargs):
        assert credentials.username == "user"
        return original(credentials, http, transport=fake.transport(), sleep=lambda _s: None)

    monkeypatch.setattr("centric_api.fetch.CentricClient", client_factory)
    monkeypatch.setattr("centric_api.maintenance.CentricClient", client_factory)
    return tmp_path


def test_fetch_json_summary_and_exit_code(workspace, capsys) -> None:
    assert cli.main(["fetch", "--json", "-q"]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert summary["endpoints"][0]["fetched"] == 1
    assert summary["silver"][0]["unmapped"] == ["extra_field"]
    assert (workspace / "centric.db").is_file()


def test_status_after_fetch(workspace, capsys) -> None:
    cli.main(["fetch", "-q"])
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0

    status = json.loads(capsys.readouterr().out)
    (styles,) = status["endpoints"]
    assert styles["active_records"] == 1
    assert styles["last_success_mode"] == "full"
    assert styles["silver_table"] == "silver_styles"
    assert styles["unmapped_fields"] == ["extra_field"]
    assert status["last_run"]["status"] == "ok"
    assert status["lock"] is None

    assert cli.main(["status"]) == 0
    assert "styles" in capsys.readouterr().out


def test_swagger_and_silver_commands(workspace, capsys) -> None:
    cli.main(["fetch", "-q"])
    capsys.readouterr()

    assert cli.main(["swagger"]) == 0
    assert "swagger v2: unchanged" in capsys.readouterr().out
    assert cli.main(["silver", "--rebuild", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "rebuilt"


def test_errors_exit_with_status_2(workspace, capsys, monkeypatch) -> None:
    assert cli.main(["fetch", "-e", "nope"]) == 2
    assert "Unknown endpoint" in capsys.readouterr().err
    (workspace / ".env").unlink()
    assert cli.main(["fetch"]) == 2
    assert "Missing CENTRIC_BASE_URL" in capsys.readouterr().err


def test_init_writes_a_loadable_config(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "my.yml"]) == 0
    assert cli.main(["--config", "my.yml", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert len(status["endpoints"]) == 42
    assert status["exists"] is False
    assert not (tmp_path / "centric.db").exists()
    assert cli.main(["init", "my.yml"]) == 2


def _signal_on_request(workspace, monkeypatch, *, signum: int, at_request: int) -> FakeCentric:
    """Six styles over pages of 2; send `signum` to ourselves on the given styles request."""
    fake = FakeCentric()
    for n in range(1, 7):
        fake.add("styles", id=f"S{n}", _modified_at=ts(n), node_name=f"Style {n}")
    inner = fake.transport()
    calls = {"styles": 0}

    def handle(request):
        if request.url.path.endswith("/v2/styles"):
            calls["styles"] += 1
            if calls["styles"] == at_request:
                os.kill(os.getpid(), signum)
        return inner.handle_request(request)

    def client_factory(credentials, http, **kwargs):
        transport = httpx.MockTransport(handle) if at_request else fake.transport()
        return CentricClient(credentials, http, transport=transport, sleep=lambda _s: None)

    monkeypatch.setattr("centric_api.fetch.CentricClient", client_factory)
    monkeypatch.setattr("centric_api.maintenance.CentricClient", client_factory)
    return fake


@pytest.mark.parametrize(
    ("signum", "status"), [(signal.SIGTERM, 143), (signal.SIGHUP, 129), (signal.SIGINT, 130)]
)
def test_stop_signal_mid_fetch_releases_lock_and_next_fetch_resumes(
    workspace, monkeypatch, capsys, signum, status
) -> None:
    _signal_on_request(workspace, monkeypatch, signum=signum, at_request=2)
    assert cli.main(["fetch", "-q"]) == status
    err = capsys.readouterr().err
    assert f"Interrupted ({signal.Signals(signum).name})" in err

    assert cli.main(["status", "--json"]) == 0
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["lock"] is None  # released: the next run doesn't wait 15 minutes
    assert 0 < stopped["endpoints"][0]["active_records"] < 6

    _signal_on_request(workspace, monkeypatch, signum=signum, at_request=0)
    assert cli.main(["fetch", "-q"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["endpoints"][0]["active_records"] == 6


def test_signal_handlers_are_restored_after_a_command(workspace) -> None:
    before = {sig: signal.getsignal(sig) for sig in cli.STOP_SIGNALS}
    assert cli.main(["fetch", "-q"]) == 0
    assert {sig: signal.getsignal(sig) for sig in cli.STOP_SIGNALS} == before
