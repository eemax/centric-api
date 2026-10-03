from __future__ import annotations

import json

import pytest

from centric_api import cli
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
