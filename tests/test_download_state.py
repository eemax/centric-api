from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import centric_api.download as download_module
from centric_api.download import run_download_job
from centric_api.store import connect
from tests.helpers_download import _Auth, _download_config, _insert_download_run, _insert_record


def test_download_dry_run_requires_existing_db_without_creating_it(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError, match="SQLite database not found"):
        run_download_job(
            db_path=db_path,
            auth_ctx=None,
            config=_download_config(tmp_path),
            dry_run=True,
        )

    assert not db_path.exists()

def test_download_run_id_checks_database_history(tmp_path: Path, monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 1, 1, tzinfo=UTC)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(download_module, "datetime", FixedDateTime)
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )
        _insert_download_run(conn, run_id="2026-01-01T000000Z-docs")

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=_download_config(tmp_path),
        dry_run=True,
    )

    assert result.run_id == "2026-01-01T000000Z-docs-2"

def test_download_delta_skips_current_revision_present_on_disk(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )

    config = _download_config(tmp_path)
    existing_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    existing_file.parent.mkdir(parents=True)
    existing_file.write_bytes(b"already here")

    sync_result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        mode="sync",
    )
    assert sync_result.selected_count == 1
    assert sync_result.already_present_count == 1

    delta_result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        dry_run=True,
    )

    assert delta_result.matched_count == 1
    assert delta_result.selected_count == 0
    assert delta_result.skipped_count == 1
    assert delta_result.skipped_current_count == 1
    assert delta_result.items[0]["status"] == "skipped_current"

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT document_id, revision_id, status, file_path
            FROM download_current
            """
        ).fetchall()
    assert rows == [("D1", "R1", "current", str(existing_file))]


def test_download_delta_redownloads_current_revision_when_file_hash_mismatches(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )

    config = _download_config(tmp_path)
    existing_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    existing_file.parent.mkdir(parents=True)
    existing_file.write_bytes(b"original")
    sync_result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        mode="sync",
    )
    assert sync_result.already_present_count == 1

    existing_file.write_bytes(b"tampered")
    delta_result = run_download_job(
        db_path=db_path,
        auth_ctx=_Auth(httpx.Response(200, content=b"fixed")),
        config=config,
        mode="delta",
    )

    assert delta_result.selected_count == 1
    assert delta_result.downloaded_count == 1
    assert delta_result.skipped_current_count == 0
    assert existing_file.read_bytes() == b"fixed"


def test_download_rebuild_redownloads_and_tombstones_unselected(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )

    config = _download_config(tmp_path)
    existing_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    existing_file.parent.mkdir(parents=True)
    existing_file.write_bytes(b"old")

    sync_result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        mode="sync",
    )
    assert sync_result.already_present_count == 1

    rebuild_result = run_download_job(
        db_path=db_path,
        auth_ctx=_Auth(httpx.Response(200, content=b"new")),
        config=config,
        mode="rebuild",
    )

    assert rebuild_result.selected_count == 1
    assert rebuild_result.downloaded_count == 1
    assert rebuild_result.superseded_count == 0
    assert existing_file.read_bytes() == b"new"

    with connect(db_path) as conn:
        conn.execute("DELETE FROM endpoint_records WHERE endpoint = 'documents'")
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D2",
            payload={"id": "D2", "node_name": "art.ai", "latest_revision": "R2"},
        )

    tombstone_result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        mode="rebuild",
    )

    assert tombstone_result.matched_count == 0
    assert tombstone_result.tombstoned_count == 1
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT document_id, revision_id, status, tombstone_reason
            FROM download_current
            """
        ).fetchall()
    assert rows == [("D1", "R1", "tombstoned", "no_longer_selected")]


def test_download_rebuild_does_not_tombstone_when_manifest_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )

    config = _download_config(tmp_path)
    existing_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    existing_file.parent.mkdir(parents=True)
    existing_file.write_bytes(b"old")
    run_download_job(db_path=db_path, auth_ctx=None, config=config, mode="sync")

    with connect(db_path) as conn:
        conn.execute("DELETE FROM endpoint_records WHERE endpoint = 'documents'")
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D2",
            payload={"id": "D2", "node_name": "art.ai", "latest_revision": "R2"},
        )

    def fail_manifest(*_args, **_kwargs):
        raise RuntimeError("manifest write failed")

    monkeypatch.setattr("centric_api.download.write_manifest", fail_manifest)

    with pytest.raises(RuntimeError, match="manifest write failed"):
        run_download_job(
            db_path=db_path,
            auth_ctx=None,
            config=config,
            mode="rebuild",
        )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT document_id, revision_id, status, tombstone_reason
            FROM download_current
            """
        ).fetchall()
    assert rows == [("D1", "R1", "current", None)]


def test_download_sync_uses_stored_content_disposition_path(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )

    config = _download_config(tmp_path)
    first = run_download_job(
        db_path=db_path,
        auth_ctx=_Auth(
            httpx.Response(
                200,
                headers={"content-disposition": 'inline;filename="real-name.pdf"'},
                content=b"hello",
            )
        ),
        config=config,
    )

    assert first.downloaded_count == 1
    assert first.items[0]["file_path"].endswith("/real-name.pdf")

    sync = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        mode="sync",
    )

    assert sync.already_present_count == 1
    assert sync.items[0]["file_path"].endswith("/real-name.pdf")

def test_download_rebuild_failure_preserves_previous_current_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("centric_api.download.time.sleep", lambda _seconds: None)
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )

    config = _download_config(tmp_path)
    old_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"old")
    run_download_job(db_path=db_path, auth_ctx=None, config=config, mode="sync")

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?
            WHERE endpoint = 'documents' AND record_id = 'D1'
            """,
            [
                json.dumps(
                    {"id": "D1", "node_name": "spec.pdf", "latest_revision": "R2"},
                    sort_keys=True,
                ),
                "hash-documents-D1-r2",
            ],
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R2",
            payload={"id": "R2", "file_name": "spec.pdf"},
        )

    result = run_download_job(
        db_path=db_path,
        auth_ctx=_Auth(httpx.Response(503, content=b"try later")),
        config=config,
        mode="rebuild",
    )

    assert result.failed_count == 1
    assert result.superseded_count == 0
    assert result.tombstoned_count == 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT revision_id, status
            FROM download_current
            ORDER BY revision_id
            """
        ).fetchall()
    assert rows == [("R1", "current"), ("R2", "failed")]
