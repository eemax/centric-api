from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from centric_api.config import ConfigError
from centric_api.download import run_download_job
from centric_api.download_config import load_download_config
from centric_api.download_http import download_revision_file
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
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "spec.pdf"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R2",
            payload={"id": "R2", "file_name": "art.ai"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R3",
            payload={"id": "R3", "file_name": "worksheet.xlsx"},
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
    assert result.dry_run_count == 2
    assert result.superseded_count == 0
    assert result.tombstoned_count == 0
    assert {item["document_id"] for item in result.items} == {"D1", "D3"}
    assert not result.manifest_path.exists()

    with sqlite3.connect(db_path) as conn:
        download_run_count = conn.execute("SELECT COUNT(*) FROM download_runs").fetchone()[0]
        download_item_count = conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]
        download_current_count = conn.execute("SELECT COUNT(*) FROM download_current").fetchone()[0]
    assert download_run_count == 0
    assert download_item_count == 0
    assert download_current_count == 0


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


def test_download_requires_cached_source_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path):
        pass

    config = _download_config(tmp_path)

    with pytest.raises(ConfigError, match="documents"):
        run_download_job(
            db_path=db_path,
            auth_ctx=None,
            config=config,
            mode="rebuild",
        )


def test_download_preflight_accepts_fetched_empty_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_applied_raw_file(conn, endpoint="documents", record_count=0)
        _insert_applied_raw_file(conn, endpoint="document_revisions", record_count=0)

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=_download_config(tmp_path),
        dry_run=True,
    )

    assert result.matched_count == 0
    assert result.selected_count == 0


def test_download_requires_cached_revisions_without_revision_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )

    with pytest.raises(ConfigError, match="document_revisions"):
        run_download_job(
            db_path=db_path,
            auth_ctx=None,
            config=_download_config(tmp_path),
            mode="rebuild",
        )


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


def test_download_uses_revision_filters_and_filename(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "display-name", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "node_name": "1", "file_name": "actual.pdf"},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D2",
            payload={"id": "D2", "node_name": "other-display", "latest_revision": "R2"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R2",
            payload={"id": "R2", "node_name": "1", "file_name": "art.ai"},
        )

    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: docs
    sources:
      - endpoint: documents
    revision_filters:
      - path: file_name
        matches: '\\.pdf$'
""",
        encoding="utf-8",
    )
    config = replace(load_download_config(config_path), output_dir=tmp_path / "downloads")

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        dry_run=True,
    )

    assert result.selected_count == 1
    assert result.items[0]["document_id"] == "D1"
    assert result.items[0]["document_name"] == "actual.pdf"
    assert result.items[0]["file_path"].endswith("/actual.pdf")


def test_download_revision_filters_require_cached_revisions(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "spec.pdf", "latest_revision": "R1"},
        )

    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: docs
    sources:
      - endpoint: documents
    revision_filters:
      - path: file_name
        matches: '\\.pdf$'
""",
        encoding="utf-8",
    )
    config = replace(load_download_config(config_path), output_dir=tmp_path / "downloads")

    with pytest.raises(ConfigError, match="document_revisions"):
        run_download_job(
            db_path=db_path,
            auth_ctx=None,
            config=config,
            mode="rebuild",
        )

    with sqlite3.connect(db_path) as conn:
        current_count = conn.execute("SELECT COUNT(*) FROM download_current").fetchone()[0]
    assert current_count == 0


def test_download_skips_documents_missing_cached_latest_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "one.pdf", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R0",
            payload={"id": "R0", "file_name": "other.pdf"},
        )
    events: list[dict] = []

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=_download_config(tmp_path),
        dry_run=True,
        log_callback=events.append,
    )

    assert result.matched_count == 0
    assert result.selected_count == 0
    assert {
        (event["event"], event.get("document_id"), event.get("revision_id")) for event in events
    } >= {("download_revision_record_missing", "D1", "R1")}


def test_download_source_filter_lookup_matches_referenced_record(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SEASON1",
            payload={"id": "SEASON1", "node_name": "SS26"},
        )
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SEASON2",
            payload={"id": "SEASON2", "node_name": "FW26"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "active": True,
                "season": "SEASON1",
                "documents": ["D1"],
                "referenced_documents": [],
            },
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S2",
            payload={
                "id": "S2",
                "active": True,
                "season": "SEASON2",
                "documents": ["D2"],
                "referenced_documents": [],
            },
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "one", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "one.pdf"},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D2",
            payload={"id": "D2", "node_name": "two", "latest_revision": "R2"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R2",
            payload={"id": "R2", "file_name": "two.pdf"},
        )

    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: ss26-style-docs
    sources:
      - endpoint: styles
        filters:
          - path: season
            lookup:
              endpoint: seasons
              path: node_name
              equals: SS26
    revision_filters:
      - path: file_name
        matches: '\\.pdf$'
""",
        encoding="utf-8",
    )
    config = replace(load_download_config(config_path), output_dir=tmp_path / "downloads")

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        dry_run=True,
    )

    assert result.selected_count == 1
    assert result.items[0]["document_id"] == "D1"


def test_download_lookup_filters_require_cached_lookup_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "season": "SEASON1",
                "documents": ["D1"],
                "referenced_documents": [],
            },
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "one.pdf", "latest_revision": "R1"},
        )

    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: ss26-style-docs
    sources:
      - endpoint: styles
        filters:
          - path: season
            lookup:
              endpoint: seasons
              path: node_name
              equals: SS26
""",
        encoding="utf-8",
    )
    config = replace(load_download_config(config_path), output_dir=tmp_path / "downloads")

    with pytest.raises(ConfigError, match="seasons"):
        run_download_job(
            db_path=db_path,
            auth_ctx=None,
            config=config,
            mode="rebuild",
        )


def test_download_lookup_filter_rejects_source_arrays(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SEASON1",
            payload={"id": "SEASON1", "node_name": "SS26"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "season": ["SEASON1"],
                "documents": ["D1"],
                "referenced_documents": [],
            },
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "one", "latest_revision": "R1"},
        )
        _insert_record(
            conn,
            endpoint="document_revisions",
            record_id="R1",
            payload={"id": "R1", "file_name": "one.pdf"},
        )

    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: no-array-lookups
    sources:
      - endpoint: styles
        filters:
          - path: season
            lookup:
              endpoint: seasons
              path: node_name
              equals: SS26
""",
        encoding="utf-8",
    )
    config = replace(load_download_config(config_path), output_dir=tmp_path / "downloads")

    result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=config,
        dry_run=True,
    )

    assert result.matched_count == 0


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


def test_download_revision_file_retries_retryable_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[int] = []
    monkeypatch.setattr("centric_api.download.time.sleep", sleeps.append)
    auth = _Auth(
        [
            httpx.Response(503, content=b"reload"),
            httpx.Response(200, content=b"ok"),
        ]
    )

    result = download_revision_file(
        auth,
        revision_id="R1",
        target_path=tmp_path / "fallback.pdf",
        fallback_filename="fallback.pdf",
    )

    assert result.path.read_bytes() == b"ok"
    assert sleeps == [15]


def test_download_config_rejects_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
output_dir: downloads
jobs:
  - name: docs
    max_documents: 5
    sources:
      - endpoint: documents
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown keys: max_documents"):
        load_download_config(config_path)


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


def _insert_applied_raw_file(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    record_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO applied_raw_files (
            file_path, endpoint, source_run_id, is_delta, record_count,
            invalid_record_count, content_sha256, manifest_path, manifest_sha256,
            run_mode, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"/tmp/{endpoint}.jsonl",
            endpoint,
            "run-1",
            0,
            record_count,
            0,
            f"hash-{endpoint}",
            None,
            None,
            "full",
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
    def __init__(self, responses: httpx.Response | list[httpx.Response]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.index = 0

    def stream(self, *_args, **_kwargs) -> _Stream:
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return _Stream(response)


class _Stream:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    def __enter__(self) -> httpx.Response:
        return self.response

    def __exit__(self, *_args) -> None:
        return None


class _Auth:
    base_url = "https://centric.example.com"

    def __init__(self, response: httpx.Response | list[httpx.Response]) -> None:
        self.client = _Client(response)

    def ensure_token(self) -> str:
        return "token"

    def refresh_token(self) -> str:
        return "token"
