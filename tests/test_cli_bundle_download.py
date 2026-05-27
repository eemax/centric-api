from __future__ import annotations

import argparse
import json

import pytest

from centric_api.cli import main
from centric_api.commands.bundle import run_bundle
from centric_api.commands.download import run_download
from centric_api.store import connect
from tests.helpers_cli import _insert_bundle_item, _insert_bundle_run


def test_download_exits_when_lock_exists(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "download.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["download"])

    assert exit_code == 1
    assert "download lock exists" in capsys.readouterr().err

def test_download_dry_run_skips_lock(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "download.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["download", "--dry-run", "--db", str(tmp_path / "missing.db")])

    assert exit_code == 1
    assert "SQLite database not found" in capsys.readouterr().err
    assert not (tmp_path / "logs" / "download.log").exists()

def test_bundle_exits_when_lock_exists(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "bundle.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["bundle"])

    assert exit_code == 1
    assert "bundle lock exists" in capsys.readouterr().err

def test_bundle_dry_run_skips_lock(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    lock_path = tmp_path / "bundle.lock"
    lock_path.write_text("locked", encoding="utf-8")

    exit_code = main(["bundle", "--dry-run", "--db", str(tmp_path / "missing.db")])

    assert exit_code == 1
    assert "SQLite database not found" in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()

def test_bundle_history_commands_use_bundle_run_id(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_bundle_run(conn, "2026-01-01T000000Z-style-bundle", "2026-01-01T00:00:00Z")
        _insert_bundle_run(conn, "2026-01-02T000000Z-style-bundle", "2026-01-02T00:00:00Z")
        _insert_bundle_item(
            conn,
            "2026-01-01T000000Z-style-bundle",
            "styles\x1fS1\x1fD1",
            "files/styles/Old/spec.pdf",
            "R1",
            "sha1",
        )
        _insert_bundle_item(
            conn,
            "2026-01-02T000000Z-style-bundle",
            "styles\x1fS1\x1fD1",
            "files/styles/New/spec.pdf",
            "R2",
            "sha2",
        )

    assert main(["bundle", "list", "--db", str(db_path), "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["run_id"] == "2026-01-02T000000Z-style-bundle"

    assert (
        main(
            [
                "bundle",
                "show",
                "2026-01-01T000000Z-style-bundle",
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["bundle_name"] == "style-bundle"

    assert (
        main(
            [
                "bundle",
                "changelog",
                "2026-01-01T000000Z-style-bundle",
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    changelog = json.loads(capsys.readouterr().out)
    assert changelog["summary"]["changed_count"] == 1
    assert changelog["to_run"]["run_id"] == "2026-01-02T000000Z-style-bundle"

def test_bundle_list_and_show_use_human_tables(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_bundle_run(conn, "2026-01-01T000000Z-style-bundle", "2026-01-01T00:00:00Z")
        _insert_bundle_item(
            conn,
            "2026-01-01T000000Z-style-bundle",
            "styles\x1fS1\x1fD1",
            "files/styles/Style One/spec.pdf",
            "R1",
            "sha1",
        )

    assert main(["bundle", "list", "--db", str(db_path)]) == 0
    list_output = capsys.readouterr().out
    assert "Bundle Runs" in list_output
    assert "Run" in list_output
    assert "Delta" in list_output
    assert "run_id=" not in list_output

    assert main(["bundle", "show", "2026-01-01T000000Z-style-bundle", "--db", str(db_path)]) == 0
    show_output = capsys.readouterr().out
    assert "Bundle Run" in show_output
    assert "Files" in show_output
    assert "Change" in show_output
    assert "files/styles/Style One/spec.pdf" in show_output
    assert "- added:" not in show_output

def test_download_interrupt_releases_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))

    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("centric_api.commands.download._run_download_unlocked", interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_download(argparse.Namespace(dry_run=False))

    assert not (tmp_path / "download.lock").exists()

def test_bundle_interrupt_releases_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))

    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("centric_api.commands.bundle._run_bundle_unlocked", interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_bundle(argparse.Namespace(action="run", dry_run=False))

    assert not (tmp_path / "bundle.lock").exists()
