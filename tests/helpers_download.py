from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx

from centric_api.download_config import load_download_config


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

def _insert_download_run(conn: sqlite3.Connection, *, run_id: str) -> None:
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
            run_id,
            "docs",
            "delta",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            "manifest.json",
            0,
            0,
            0,
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
