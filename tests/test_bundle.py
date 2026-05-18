from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from centric_api.bundle import load_bundle_config, run_bundle_job
from centric_api.config import ConfigError
from centric_api.download import ensure_download_tables
from centric_api.store import connect


def test_bundle_duplicates_shared_file_under_each_source_label(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"tech pack")

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "style_code": "STY-001", "node_name": "Linen Shirt"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S2",
            payload={"id": "S2", "style_code": "STY-002", "node_name": "Linen Shirt Alt"},
        )
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R1",
            file_path=source_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
                {"endpoint": "styles", "record_id": "S2", "document_path": "documents"},
            ],
        )

    config = _bundle_config(tmp_path)

    result = run_bundle_job(
        db_path=db_path,
        config=config,
        job_name="style-bundle",
    )

    assert result.item_count == 2
    assert result.added_count == 2
    assert result.zip_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    archive_paths = {item["archive_path"] for item in manifest["items"]}
    assert "source_file_path" not in manifest["items"][0]
    assert "target_path" not in manifest["items"][0]
    assert archive_paths == {
        "files/styles/STY-001 - Linen Shirt/spec.pdf",
        "files/styles/STY-002 - Linen Shirt Alt/spec.pdf",
    }
    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
    assert "files/styles/STY-001 - Linen Shirt/spec.pdf" in names
    assert "files/styles/STY-002 - Linen Shirt Alt/spec.pdf" in names
    assert "manifest.json" in names
    assert "changelog.md" in names

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT archive_path FROM bundle_current ORDER BY archive_path"
        ).fetchall()
    assert [row[0] for row in rows] == sorted(archive_paths)


def test_bundle_changelog_tracks_changed_and_removed_files(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    first_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    second_file = tmp_path / "downloads" / "files" / "D1" / "R2" / "spec.pdf"
    first_file.parent.mkdir(parents=True)
    second_file.parent.mkdir(parents=True)
    first_file.write_bytes(b"first")
    second_file.write_bytes(b"second")

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "style_code": "STY-001", "node_name": "Linen Shirt"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S2",
            payload={"id": "S2", "style_code": "STY-002", "node_name": "Linen Shirt Alt"},
        )
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R1",
            file_path=first_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
                {"endpoint": "styles", "record_id": "S2", "document_path": "documents"},
            ],
        )

    config = _bundle_config(tmp_path)
    first = run_bundle_job(db_path=db_path, config=config, job_name="style-bundle")

    with connect(db_path) as conn:
        conn.execute("DELETE FROM download_current")
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R2",
            file_path=second_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
            ],
        )

    second = run_bundle_job(db_path=db_path, config=config, job_name="style-bundle")
    changelog = json.loads(second.changelog_json_path.read_text(encoding="utf-8"))

    assert first.added_count == 2
    assert second.changed_count == 1
    assert second.removed_count == 1
    assert changelog["previous_run_id"] == first.run_id
    assert {
        (item["change_type"], item["archive_path"]) for item in changelog["items"]
    } == {
        ("changed", "files/styles/STY-001 - Linen Shirt/spec.pdf"),
        ("removed", "files/styles/STY-002 - Linen Shirt Alt/spec.pdf"),
    }


def test_bundle_changelog_tracks_renamed_paths_by_stable_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"same file")

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "style_code": "STY-001", "node_name": "Old Name"},
        )
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R1",
            file_path=source_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
            ],
        )

    config = _bundle_config(tmp_path)
    first = run_bundle_job(db_path=db_path, config=config, job_name="style-bundle")

    with connect(db_path) as conn:
        payload_json = json.dumps(
            {"id": "S1", "style_code": "STY-001", "node_name": "New Name"},
            sort_keys=True,
        )
        conn.execute(
            """
            UPDATE endpoint_records
            SET payload_json = ?, payload_sha256 = ?
            WHERE endpoint = ? AND record_id = ?
            """,
            [payload_json, "hash-renamed", "styles", "S1"],
        )

    second = run_bundle_job(db_path=db_path, config=config, job_name="style-bundle")
    changelog = json.loads(second.changelog_json_path.read_text(encoding="utf-8"))

    assert first.added_count == 1
    assert second.renamed_count == 1
    assert second.added_count == 0
    assert second.removed_count == 0
    assert changelog["summary"]["renamed"] == 1
    assert changelog["items"] == [
        {
            "archive_path": "files/styles/STY-001 - New Name/spec.pdf",
            "change_type": "renamed",
            "document_id": "D1",
            "previous_archive_path": "files/styles/STY-001 - Old Name/spec.pdf",
            "previous_revision_id": "R1",
            "previous_sha256": _sha256(b"same file"),
            "revision_id": "R1",
            "sha256": _sha256(b"same file"),
            "source_endpoint": "styles",
            "source_label": "STY-001 - New Name",
            "source_record_id": "S1",
        }
    ]


def test_bundle_dry_run_does_not_write_artifacts_or_state(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"dry")

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "style_code": "STY-001", "node_name": "Linen Shirt"},
        )
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R1",
            file_path=source_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
            ],
        )

    result = run_bundle_job(
        db_path=db_path,
        config=_bundle_config(tmp_path),
        job_name="style-bundle",
        dry_run=True,
    )

    assert result.added_count == 1
    assert result.zip_path is None
    assert not result.manifest_path.exists()
    assert not result.changelog_json_path.exists()
    assert not result.changelog_md_path.exists()
    assert not (tmp_path / "bundles" / "runs").exists()
    with sqlite3.connect(db_path) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM bundle_runs").fetchone()[0]
        current_count = conn.execute("SELECT COUNT(*) FROM bundle_current").fetchone()[0]
    assert run_count == 0
    assert current_count == 0


def test_bundle_fails_when_current_download_file_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    missing_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "missing.pdf"
    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "style_code": "STY-001", "node_name": "Linen Shirt"},
        )
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R1",
            file_path=missing_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
            ],
        )

    with pytest.raises(ConfigError, match="missing downloaded files"):
        run_bundle_job(
            db_path=db_path,
            config=_bundle_config(tmp_path),
            job_name="style-bundle",
        )


def _bundle_config(tmp_path: Path):
    config_path = tmp_path / "bundle.yml"
    config_path.write_text(
        """
version: 1
output_dir: bundles
bundles:
  - name: style-bundle
    download_job: style-docs
    layout:
      source_label:
        styles:
          fields:
            - style_code
            - node_name
          join: " - "
""",
        encoding="utf-8",
    )
    return replace(load_bundle_config(config_path), output_dir=tmp_path / "bundles")


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


def _insert_download_current(
    conn: sqlite3.Connection,
    *,
    job_name: str,
    document_id: str,
    revision_id: str,
    file_path: Path,
    source_refs: list[dict],
) -> None:
    payload = file_path.read_bytes() if file_path.is_file() else b""
    conn.execute(
        """
        INSERT INTO download_current (
            job_name, document_id, revision_id, document_name, current_revision_id,
            document_modified_at, status, file_path, sha256, bytes, last_run_id,
            selected_at, tombstoned_at, tombstone_reason, source_refs_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
        """,
        [
            job_name,
            document_id,
            revision_id,
            file_path.name,
            revision_id,
            "2026-01-01T00:00:00Z",
            "current",
            str(file_path),
            _sha256(payload),
            len(payload),
            "download-run-1",
            "2026-01-01T00:00:00Z",
            json.dumps(source_refs, sort_keys=True),
        ],
    )


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
