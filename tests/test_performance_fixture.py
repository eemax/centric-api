from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from centric_api.bundle import compare_bundle_runs, run_bundle_job
from centric_api.bundle_config import BundleConfig, BundleJob
from centric_api.changelog import record_changelog
from centric_api.db_schema import ensure_download_tables
from centric_api.download import run_download_job
from centric_api.download_config import DownloadConfig, DownloadJob, DownloadSource
from centric_api.store import connect, ingest_raw_dir

pytestmark = pytest.mark.skipif(
    os.environ.get("CENTRIC_API_PERF") != "1",
    reason="set CENTRIC_API_PERF=1 to run the synthetic performance fixture",
)


def test_synthetic_raw_changelog_download_and_bundle_perf(tmp_path: Path) -> None:
    record_count = int(os.environ.get("CENTRIC_API_PERF_RECORDS", "10000"))
    raw_run_dir = tmp_path / "raw" / "runs" / "perf"
    db_path = tmp_path / "centric.db"

    timings: dict[str, float] = {}
    _write_synthetic_raw_run(raw_run_dir, record_count)

    started = time.perf_counter()
    ingest_result = ingest_raw_dir(raw_run_dir, db_path, schemas={})
    timings["ingest_seconds"] = time.perf_counter() - started
    assert ingest_result.records_read == record_count * 3

    started = time.perf_counter()
    changelog_run = record_changelog(db_path, full=True)
    timings["changelog_seconds"] = time.perf_counter() - started
    assert changelog_run.record_count == record_count * 3

    download_config = DownloadConfig(
        path=tmp_path / "download.yml",
        output_dir=tmp_path / "downloads",
        jobs=(
            DownloadJob(
                name="style-docs",
                sources=(DownloadSource(endpoint="styles"),),
            ),
        ),
    )
    started = time.perf_counter()
    download_result = run_download_job(
        db_path=db_path,
        auth_ctx=None,
        config=download_config,
        job_name="style-docs",
        dry_run=True,
    )
    timings["download_selection_seconds"] = time.perf_counter() - started
    assert download_result.selected_count == record_count

    _seed_download_current(db_path, tmp_path / "download-files", record_count)
    bundle_config = BundleConfig(
        path=tmp_path / "bundle.yml",
        output_dir=tmp_path / "bundles",
        bundles=(BundleJob(name="style-docs", download_job="style-docs"),),
    )

    first_bundle = run_bundle_job(
        db_path=db_path,
        config=bundle_config,
        job_name="style-docs",
        zip_bundle=False,
    )
    _change_one_downloaded_revision(db_path, tmp_path / "download-files")
    second_bundle = run_bundle_job(
        db_path=db_path,
        config=bundle_config,
        job_name="style-docs",
        zip_bundle=False,
    )

    started = time.perf_counter()
    comparison = compare_bundle_runs(
        db_path,
        from_run_id=first_bundle.run_id,
        to_run_id=second_bundle.run_id,
    )
    timings["bundle_comparison_seconds"] = time.perf_counter() - started
    assert comparison.summary["changed_count"] == 1
    assert len(comparison.items) == record_count

    print(json.dumps({"records": record_count, **timings}, sort_keys=True))


def _write_synthetic_raw_run(raw_run_dir: Path, record_count: int) -> None:
    raw_run_dir.mkdir(parents=True)
    now = "2026-05-20T00:00:00.000Z"
    _write_jsonl(
        raw_run_dir / "styles.jsonl",
        (
            {
                "id": f"S{i:06d}",
                "node_name": f"Style {i:06d}",
                "style_code": f"STY-{i:06d}",
                "documents": [f"D{i:06d}"],
                "_modified_at": now,
                "modified_by": "U000001",
            }
            for i in range(record_count)
        ),
    )
    _write_jsonl(
        raw_run_dir / "documents.jsonl",
        (
            {
                "id": f"D{i:06d}",
                "node_name": f"Document {i:06d}.pdf",
                "latest_revision": f"R{i:06d}",
                "_modified_at": now,
                "modified_by": "U000001",
            }
            for i in range(record_count)
        ),
    )
    _write_jsonl(
        raw_run_dir / "document_revisions.jsonl",
        (
            {
                "id": f"R{i:06d}",
                "file_name": f"Document {i:06d}.pdf",
                "_modified_at": now,
            }
            for i in range(record_count)
        ),
    )


def _write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _seed_download_current(db_path: Path, files_dir: Path, record_count: int) -> None:
    files_dir.mkdir(parents=True)
    rows = []
    for i in range(record_count):
        path = files_dir / f"D{i:06d}.pdf"
        path.write_bytes(f"document {i}\n".encode())
        rows.append(
            (
                "style-docs",
                f"D{i:06d}",
                f"R{i:06d}",
                f"Document {i:06d}.pdf",
                f"R{i:06d}",
                "2026-05-20T00:00:00.000Z",
                "current",
                str(path),
                None,
                path.stat().st_size,
                "perf-run",
                "2026-05-20T00:00:00.000Z",
                None,
                None,
                json.dumps([{"endpoint": "styles", "record_id": f"S{i:06d}"}]),
            )
        )
    with connect(db_path) as conn:
        ensure_download_tables(conn)
        conn.executemany(
            """
            INSERT INTO download_current (
                job_name, document_id, revision_id, document_name, current_revision_id,
                document_modified_at, status, file_path, sha256, bytes, last_run_id,
                selected_at, tombstoned_at, tombstone_reason, source_refs_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _change_one_downloaded_revision(db_path: Path, files_dir: Path) -> None:
    path = files_dir / "D000000-v2.pdf"
    path.write_bytes(b"document 0 changed\n")
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE download_current
            SET revision_id = ?, current_revision_id = ?, file_path = ?, sha256 = NULL, bytes = ?
            WHERE job_name = ? AND document_id = ?
            """,
            ["R000000-v2", "R000000-v2", str(path), path.stat().st_size, "style-docs", "D000000"],
        )
