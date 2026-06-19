from __future__ import annotations

import argparse
import json

import pytest

from centric_api.bundle import BundleRunResult
from centric_api.cli import main
from centric_api.commands.bundle import run_bundle
from centric_api.commands.download import run_download
from centric_api.download import DownloadRunResult
from centric_api.rendering.bundle import print_human_bundle_summary
from centric_api.rendering.download import print_human_download_summary
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
        _insert_bundle_run(conn, "style-bundle-2026-01-01-0000", "2026-01-01T00:00:00Z")
        _insert_bundle_run(conn, "style-bundle-2026-01-02-0000", "2026-01-02T00:00:00Z")
        _insert_bundle_item(
            conn,
            "style-bundle-2026-01-01-0000",
            "styles\x1fS1\x1fD1",
            "files/styles/Old/spec.pdf",
            "R1",
            "sha1",
        )
        _insert_bundle_item(
            conn,
            "style-bundle-2026-01-02-0000",
            "styles\x1fS1\x1fD1",
            "files/styles/New/spec.pdf",
            "R2",
            "sha2",
        )

    assert main(["bundle", "list", "--db", str(db_path), "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["run_id"] == "style-bundle-2026-01-02-0000"

    assert (
        main(
            [
                "bundle",
                "show",
                "style-bundle-2026-01-01-0000",
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
                "style-bundle-2026-01-01-0000",
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    changelog = json.loads(capsys.readouterr().out)
    assert changelog["summary"]["changed_count"] == 1
    assert changelog["to_run"]["run_id"] == "style-bundle-2026-01-02-0000"


def test_bundle_list_and_show_use_human_tables(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_bundle_run(conn, "style-bundle-2026-01-01-0000", "2026-01-01T00:00:00Z")
        _insert_bundle_item(
            conn,
            "style-bundle-2026-01-01-0000",
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

    assert main(["bundle", "show", "style-bundle-2026-01-01-0000", "--db", str(db_path)]) == 0
    show_output = capsys.readouterr().out
    assert "Bundle Run" in show_output
    assert "Files" in show_output
    assert "Change" in show_output
    assert "files/styles/Style One/spec.pdf" in show_output
    assert "- added:" not in show_output


def test_download_summary_formats_counts_and_labels_item_preview(tmp_path, capsys) -> None:
    result = DownloadRunResult(
        run_id="run-1",
        job_name="docs",
        mode="delta",
        manifest_path=tmp_path / "manifest.json",
        matched_count=1234,
        selected_count=1200,
        downloaded_count=1100,
        already_present_count=100,
        failed_count=0,
        skipped_count=34,
        skipped_current_count=12,
        dry_run_count=0,
        superseded_count=2,
        tombstoned_count=1,
        dry_run=False,
        items=tuple(
            {
                "document_id": f"D{index}",
                "latest_revision_id": f"R{index}",
                "status": "downloaded",
            }
            for index in range(12)
        ),
    )

    print_human_download_summary(result)

    output = capsys.readouterr().out
    assert "Matched:         1,234" in output
    assert "Downloaded:      1,100" in output
    assert "Item Preview: first 10 of 12" in output
    assert "... 2 more" in output


def test_bundle_summary_formats_counts(tmp_path, capsys) -> None:
    result = BundleRunResult(
        run_id="run-1",
        bundle_name="style-bundle",
        download_job="docs",
        manifest_path=tmp_path / "manifest.json",
        changelog_json_path=tmp_path / "changelog.json",
        changelog_md_path=tmp_path / "changelog.md",
        zip_path=None,
        item_count=1234,
        added_count=1100,
        changed_count=100,
        renamed_count=20,
        removed_count=10,
        unchanged_count=4,
        missing_count=0,
        dry_run=False,
    )

    print_human_bundle_summary(result)

    output = capsys.readouterr().out
    assert "Items:     1,234" in output
    assert "Added:     1,100" in output
    assert "Changed:   100" in output


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
