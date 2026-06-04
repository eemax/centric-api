from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from centric_api.config import ConfigError
from centric_api.download import run_download_job
from centric_api.download_config import load_download_config
from centric_api.store import connect
from tests.helpers_download import _download_config, _insert_applied_raw_file, _insert_record


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


def test_download_config_requires_non_empty_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "download.yml"
    config_path.write_text(
        """
version: 1
jobs:
  - name: docs
    sources: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="sources must be a non-empty array"):
        load_download_config(config_path)
