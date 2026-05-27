from __future__ import annotations

import json

from centric_api.changelog import ChangelogRun
from centric_api.models import AuthSettings, CountSpec, EndpointSpec, FetcherConfig, FetchRunResult
from centric_api.store import IngestResult


def _insert_bundle_run(conn, run_id: str, finished_at: str) -> None:
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
            finished_at,
            finished_at,
            "manifest.json",
            "changelog.json",
            "changelog.md",
            f"{run_id}.zip",
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
    )

def _insert_download_run(conn) -> None:
    conn.execute(
        """
        INSERT INTO download_runs (
            run_id, job_name, mode, started_at, finished_at, manifest_path,
            matched_count, selected_count, downloaded_count, already_present_count,
            failed_count, skipped_count, skipped_current_count, dry_run_count,
            superseded_count, tombstoned_count, dry_run
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "download-1",
            "docs",
            "delta",
            "2026-01-02T00:00:00Z",
            "2026-01-02T00:00:00Z",
            "manifest.json",
            4,
            4,
            4,
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

def _insert_endpoint_record(
    conn,
    *,
    endpoint: str,
    record_id: str,
    payload: dict[str, object],
    payload_hash: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            record_id,
            json.dumps(payload, sort_keys=True),
            payload_hash,
            payload.get("_modified_at"),
            f"{endpoint}.jsonl",
            "run-1",
            "2026-01-01T00:00:00Z",
        ],
    )

def _patch_fetch_pipeline(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    endpoint = EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        count_spec=CountSpec(path="count/Style"),
    )
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.load_fetcher_settings",
        lambda _path: (fetcher_cfg, AuthSettings(timeout=1), [endpoint]),
    )

    class Auth:
        base_url = "https://centric.example.com"
        timeout = 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "centric_api.commands.fetch.init_auth_context",
        lambda *_args, **_kwargs: Auth(),
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.run_endpoint",
        lambda *_args, **_kwargs: FetchRunResult(
            endpoint="styles",
            pages_fetched=1,
            items_fetched=1,
            expected_count=1,
            retries_used=0,
            start_skip=0,
            next_skip=50,
            duration_seconds=0.1,
            output_file=tmp_path / "raw" / "styles.jsonl",
            checkpoint_file=tmp_path / "checkpoints" / "styles.json",
            id_validation_checked_items=1,
            id_validation_unique_ids=1,
        ),
    )
    monkeypatch.setattr(
        "centric_api.commands.fetch.ingest_raw_dir",
        lambda *_args, **_kwargs: IngestResult(
            applied_files=1,
            skipped_files=0,
            records_read=1,
            records_upserted=1,
            records_deleted=0,
            records_hard_deleted=0,
            invalid_records=0,
            endpoints={"styles": 1},
            upserted_record_ids_by_endpoint={"styles": ("S1",)},
            deleted_record_ids_by_endpoint={},
            deleted_record_delete_types_by_endpoint={},
        ),
    )

    def fake_record_changelog(*_args, progress=None, **_kwargs):
        if progress is not None:
            progress("Mode: scoped refresh")
            progress("Writing changelog tables...")
        return ChangelogRun(
            run_id="changelog-1",
            endpoint_count=1,
            record_count=1,
            event_count=1,
            full_refresh=False,
            scoped_record_count=1,
        )

    monkeypatch.setattr("centric_api.commands.fetch.record_changelog", fake_record_changelog)

def _insert_bundle_item(
    conn,
    run_id: str,
    identity: str,
    archive_path: str,
    revision_id: str,
    sha256: str,
) -> None:
    conn.execute(
        """
        INSERT INTO bundle_items (
            run_id, bundle_name, archive_path, identity, source_endpoint,
            source_record_id, source_label, document_id, revision_id, file_path,
            sha256, bytes, status, change_type, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            "style-bundle",
            archive_path,
            identity,
            "styles",
            "S1",
            "Style One",
            "D1",
            revision_id,
            "/tmp/spec.pdf",
            sha256,
            10,
            "included",
            "added",
            "2026-01-01T00:00:00Z",
        ],
    )

def _insert_applied_raw_file(conn, *, endpoint: str, record_count: int = 0) -> None:
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
