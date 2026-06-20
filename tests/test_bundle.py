from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import centric_api.bundle as bundle_module
from centric_api.bundle import run_bundle_job
from centric_api.bundle_config import load_bundle_config
from centric_api.bundle_state import compare_bundle_runs
from centric_api.config import ConfigError
from centric_api.db_schema import ensure_download_tables
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
        history_rows = conn.execute(
            """
            SELECT archive_path, identity, source_endpoint, source_record_id, source_label
            FROM bundle_items
            ORDER BY archive_path
            """
        ).fetchall()
    assert [row[0] for row in rows] == sorted(archive_paths)
    assert history_rows == [
        (
            "files/styles/STY-001 - Linen Shirt/spec.pdf",
            "styles\x1fS1\x1fD1",
            "styles",
            "S1",
            "STY-001 - Linen Shirt",
        ),
        (
            "files/styles/STY-002 - Linen Shirt Alt/spec.pdf",
            "styles\x1fS2\x1fD1",
            "styles",
            "S2",
            "STY-002 - Linen Shirt Alt",
        ),
    ]


def test_bundle_dedupes_duplicate_source_document_refs(tmp_path: Path) -> None:
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
        _insert_download_current(
            conn,
            job_name="style-docs",
            document_id="D1",
            revision_id="R1",
            file_path=source_file,
            source_refs=[
                {"endpoint": "styles", "record_id": "S1", "document_path": "documents"},
                {
                    "endpoint": "styles",
                    "record_id": "S1",
                    "document_path": "referenced_documents",
                },
            ],
        )

    result = run_bundle_job(
        db_path=db_path,
        config=_bundle_config(tmp_path),
        job_name="style-bundle",
    )

    assert result.item_count == 1
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert [item["archive_path"] for item in manifest["items"]] == [
        "files/styles/STY-001 - Linen Shirt/spec.pdf"
    ]


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
    assert {(item["change_type"], item["archive_path"]) for item in changelog["items"]} == {
        ("changed", "files/styles/STY-001 - Linen Shirt/spec.pdf"),
        ("removed", "files/styles/STY-002 - Linen Shirt Alt/spec.pdf"),
    }
    comparison = compare_bundle_runs(db_path, from_run_id=first.run_id, to_run_id=second.run_id)
    assert comparison.summary["changed_count"] == 1
    assert comparison.summary["removed_count"] == 1
    assert {
        (item["change_type"], item["archive_path"])
        for item in comparison.items
        if item["change_type"] != "unchanged"
    } == {
        ("changed", "files/styles/STY-001 - Linen Shirt/spec.pdf"),
        ("removed", "files/styles/STY-002 - Linen Shirt Alt/spec.pdf"),
    }
    with pytest.raises(ConfigError, match="older"):
        compare_bundle_runs(db_path, from_run_id=second.run_id, to_run_id=first.run_id)


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
        bundle_run_count = conn.execute("SELECT COUNT(*) FROM bundle_runs").fetchone()[0]
        bundle_item_count = conn.execute("SELECT COUNT(*) FROM bundle_items").fetchone()[0]
        bundle_current_count = conn.execute("SELECT COUNT(*) FROM bundle_current").fetchone()[0]
    assert bundle_run_count == 0
    assert bundle_item_count == 0
    assert bundle_current_count == 0


def test_bundle_run_id_checks_database_history(tmp_path: Path, monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 1, 1, tzinfo=UTC)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(bundle_module, "datetime", FixedDateTime)
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"dry")

    with connect(db_path) as conn:
        ensure_download_tables(conn)
        _insert_bundle_run(conn, run_id="style-bundle-2026-01-01-0000")
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

    assert result.run_id == "style-bundle-2026-01-01-0000-2"


def test_bundle_dry_run_requires_existing_db_without_creating_it(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError, match="SQLite database not found"):
        run_bundle_job(
            db_path=db_path,
            config=_bundle_config(tmp_path),
            job_name="style-bundle",
            dry_run=True,
        )

    assert not db_path.exists()


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


def test_bundle_fails_when_current_download_file_hash_mismatches(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"expected")
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
    source_file.write_bytes(b"tampered")

    with pytest.raises(ConfigError, match="missing downloaded files"):
        run_bundle_job(
            db_path=db_path,
            config=_bundle_config(tmp_path),
            job_name="style-bundle",
        )


def test_bundle_cleans_stale_temp_dir_before_writing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 1, 1, tzinfo=UTC)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(bundle_module, "datetime", FixedDateTime)
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"tech pack")
    stale_file = (
        tmp_path / "bundles" / "runs" / ".style-bundle-2026-01-01-0000.tmp" / "files" / "stale.txt"
    )
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("stale", encoding="utf-8")

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
    )

    assert not (result.manifest_path.parent / "files" / "stale.txt").exists()
    assert result.zip_path is not None
    with zipfile.ZipFile(result.zip_path) as archive:
        assert "files/stale.txt" not in archive.namelist()


def test_bundle_fails_when_copied_file_does_not_match_manifest_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "centric.db"
    source_file = tmp_path / "downloads" / "files" / "D1" / "R1" / "spec.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"expected")

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

    def corrupt_copy(_source: str, target: Path | str) -> None:
        Path(target).write_bytes(b"corrupt")

    monkeypatch.setattr("centric_api.bundle_artifacts.shutil.copy2", corrupt_copy)

    with pytest.raises(RuntimeError, match="copied bundle file .* mismatch"):
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


def _insert_bundle_run(conn: sqlite3.Connection, *, run_id: str) -> None:
    conn.execute(
        """
        INSERT INTO bundle_runs (
            run_id, bundle_name, download_job, started_at, finished_at,
            manifest_path, changelog_json_path, changelog_md_path, zip_path,
            item_count, added_count, changed_count, renamed_count, removed_count,
            unchanged_count, missing_count, dry_run
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            "style-bundle",
            "style-docs",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            "manifest.json",
            "changelog.json",
            "changelog.md",
            f"{run_id}.zip",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
    )


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
