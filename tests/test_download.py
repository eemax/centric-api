from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx

from centric_api.download import (
    download_revision_file,
    load_download_config,
    run_download_job,
)
from centric_api.store import connect


def test_download_job_selects_style_documents_and_writes_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "active": True,
                "documents": ["D1", "D2"],
                "referenced_documents": {"front": "D3"},
            },
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S2",
            payload={"id": "S2", "active": False, "documents": ["D4"]},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D2",
            payload={"id": "D2", "node_name": "art.ai", "latest_revision": "R2"},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D3",
            payload={"id": "D3", "node_name": "worksheet.xlsx", "latest_revision": "R3"},
        )

    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: style-docs
    sources:
      - endpoint: styles
        filters:
          - path: active
            equals: true
        document_paths:
          - documents
          - referenced_documents
    document_filters:
      - path: node_name
        matches: '\\.(pdf|xlsx)$'
""",
        encoding="utf-8",
    )
    config = load_download_config(config_path)
    config = replace(config, output_dir=tmp_path / "downloads")

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        job_name="style-docs",
        dry_run=True,
    )

    assert result.mode == "delta"
    assert result.matched_count == 2
    assert result.selected_count == 2
    assert result.skipped_count == 2
    assert result.skipped_current_count == 0
    assert result.tombstoned_count == 0
    assert {item["document_id"] for item in result.items} == {"D1", "D3"}
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["mode"] == "delta"
    assert manifest["matched_count"] == 2
    assert manifest["items"][0]["status"] == "dry_run"

    with sqlite3.connect(db_path) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM download_runs").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]
        current_count = conn.execute("SELECT COUNT(*) FROM download_current").fetchone()[0]
    assert run_count == 1
    assert item_count == 2
    assert current_count == 0


def test_download_delta_skips_current_revision_present_on_disk(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
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


def test_download_rebuild_redownloads_and_tombstones_unselected(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
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
    assert existing_file.read_bytes() == b"new"

    with connect(db_path) as conn:
        conn.execute("DELETE FROM endpoint_records WHERE endpoint = 'documents'")

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


def test_download_revision_file_uses_content_disposition_filename(tmp_path: Path) -> None:
    auth = _Auth(
        httpx.Response(
            200,
            headers={
                "content-disposition": 'inline;filename="real-name.pdf"',
                "content-type": "application/pdf",
            },
            content=b"hello",
        )
    )

    result = download_revision_file(
        auth,
        revision_id="R1",
        target_path=tmp_path / "fallback.pdf",
        fallback_filename="fallback.pdf",
    )

    assert result.path == tmp_path / "real-name.pdf"
    assert result.path.read_bytes() == b"hello"
    assert result.bytes_written == 5
    assert result.content_type == "application/pdf"


def _insert_record(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    record_id: str,
    payload: dict,
) -> None:
    payload_json = json.dumps(payload, sort_keys=True)
    conn.execute(
        """
        INSERT INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            record_id,
            payload_json,
            f"hash-{endpoint}-{record_id}",
            None,
            "raw.jsonl",
            "run-1",
            "2026-01-01T00:00:00Z",
        ],
    )


def _download_config(tmp_path: Path):
    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: docs
    sources:
      - endpoint: documents
    document_filters:
      - path: node_name
        matches: '\\.pdf$'
""",
        encoding="utf-8",
    )
    config = load_download_config(config_path)
    return replace(config, output_dir=tmp_path / "downloads")


class _Client:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    def stream(self, *_args, **_kwargs) -> _Stream:
        return _Stream(self.response)


class _Stream:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    def __enter__(self) -> httpx.Response:
        return self.response

    def __exit__(self, *_args) -> None:
        return None


class _Auth:
    base_url = "https://centric.example.com"

    def __init__(self, response: httpx.Response) -> None:
        self.client = _Client(response)

    def ensure_token(self) -> str:
        return "token"

    def refresh_token(self) -> str:
        return "token"
