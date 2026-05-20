from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .download_selection import ResolvedDocument


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "manifest.json"
    temp_path = run_dir / ".manifest.json.tmp"
    temp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)
    return path


def record_download_run(
    conn: sqlite3.Connection,
    *,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
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
            manifest["run_id"],
            manifest["job"],
            manifest["mode"],
            manifest["started_at"],
            manifest["finished_at"],
            str(manifest_path),
            manifest["matched_count"],
            manifest["selected_count"],
            manifest["downloaded_count"],
            manifest["already_present_count"],
            manifest["failed_count"],
            manifest["skipped_count"],
            manifest["skipped_current_count"],
            manifest["dry_run_count"],
            manifest["superseded_count"],
            manifest["tombstoned_count"],
            int(bool(manifest["dry_run"])),
        ],
    )
    rows = []
    created_at = manifest["finished_at"]
    for item in manifest["items"]:
        rows.append(
            [
                manifest["run_id"],
                manifest["job"],
                item["document_id"],
                item.get("document_name"),
                item["latest_revision_id"],
                item.get("current_revision_id"),
                item.get("document_modified_at"),
                int(bool(item.get("latest_at_run"))),
                item.get("previous_downloaded_revision_id"),
                int(bool(item.get("previous_was_outdated"))),
                item["status"],
                item.get("file_path"),
                item.get("sha256"),
                item.get("bytes"),
                item.get("error"),
                json.dumps(item.get("source_refs", []), sort_keys=True),
                created_at,
            ]
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO download_items (
                run_id, job_name, document_id, document_name, revision_id,
                current_revision_id, document_modified_at, latest_at_run,
                previous_downloaded_revision_id, previous_was_outdated, status,
                file_path, sha256, bytes, error, source_refs_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def update_download_current(conn: sqlite3.Connection, *, manifest: dict[str, Any]) -> None:
    now = manifest["finished_at"]
    for item in manifest["items"]:
        if item["status"] in {"downloaded", "already_present"}:
            _mark_superseded_current(conn, manifest=manifest, item=item, now=now)
            conn.execute(
                """
                INSERT INTO download_current (
                    job_name, document_id, revision_id, document_name,
                    current_revision_id, document_modified_at, status, file_path,
                    sha256, bytes, last_run_id, selected_at, tombstoned_at,
                    tombstone_reason, source_refs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(job_name, document_id, revision_id) DO UPDATE SET
                    document_name = excluded.document_name,
                    current_revision_id = excluded.current_revision_id,
                    document_modified_at = excluded.document_modified_at,
                    status = excluded.status,
                    file_path = excluded.file_path,
                    sha256 = excluded.sha256,
                    bytes = excluded.bytes,
                    last_run_id = excluded.last_run_id,
                    selected_at = excluded.selected_at,
                    tombstoned_at = NULL,
                    tombstone_reason = NULL,
                    source_refs_json = excluded.source_refs_json
                """,
                [
                    manifest["job"],
                    item["document_id"],
                    item["latest_revision_id"],
                    item.get("document_name"),
                    item.get("current_revision_id"),
                    item.get("document_modified_at"),
                    "current",
                    item.get("file_path"),
                    item.get("sha256"),
                    item.get("bytes"),
                    manifest["run_id"],
                    now,
                    json.dumps(item.get("source_refs", []), sort_keys=True),
                ],
            )
        elif item["status"] == "failed":
            conn.execute(
                """
                INSERT INTO download_current (
                    job_name, document_id, revision_id, document_name,
                    current_revision_id, document_modified_at, status, file_path,
                    sha256, bytes, last_run_id, selected_at, tombstoned_at,
                    tombstone_reason, source_refs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL, ?)
                ON CONFLICT(job_name, document_id, revision_id) DO UPDATE SET
                    document_name = excluded.document_name,
                    current_revision_id = excluded.current_revision_id,
                    document_modified_at = excluded.document_modified_at,
                    status = excluded.status,
                    file_path = excluded.file_path,
                    last_run_id = excluded.last_run_id,
                    selected_at = excluded.selected_at,
                    source_refs_json = excluded.source_refs_json
                """,
                [
                    manifest["job"],
                    item["document_id"],
                    item["latest_revision_id"],
                    item.get("document_name"),
                    item.get("current_revision_id"),
                    item.get("document_modified_at"),
                    "failed",
                    item.get("file_path"),
                    manifest["run_id"],
                    now,
                    json.dumps(item.get("source_refs", []), sort_keys=True),
                ],
            )


def tombstone_unselected_current(
    conn: sqlite3.Connection,
    *,
    job_name: str,
    desired_documents: dict[str, ResolvedDocument],
    run_id: str,
    tombstoned_at: str,
) -> int:
    rows = conn.execute(
        """
        SELECT document_id, revision_id
        FROM download_current
        WHERE job_name = ? AND status = 'current'
        """,
        [job_name],
    ).fetchall()
    tombstone_rows: list[tuple[str, str, str]] = []
    for row in rows:
        document_id = str(row["document_id"])
        revision_id = str(row["revision_id"])
        desired = desired_documents.get(document_id)
        if desired is None:
            tombstone_rows.append((document_id, revision_id, "no_longer_selected"))
    if not tombstone_rows:
        return 0
    conn.executemany(
        """
        UPDATE download_current
        SET status = 'tombstoned',
            last_run_id = ?,
            tombstoned_at = ?,
            tombstone_reason = ?
        WHERE job_name = ? AND document_id = ? AND revision_id = ?
        """,
        [
            [run_id, tombstoned_at, reason, job_name, document_id, revision_id]
            for document_id, revision_id, reason in tombstone_rows
        ],
    )
    return len(tombstone_rows)


def _mark_superseded_current(
    conn: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    item: dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE download_current
        SET status = 'superseded',
            last_run_id = ?,
            tombstoned_at = ?,
            tombstone_reason = 'revision_superseded'
        WHERE job_name = ?
          AND document_id = ?
          AND revision_id <> ?
          AND status = 'current'
        """,
        [
            manifest["run_id"],
            now,
            manifest["job"],
            item["document_id"],
            item["latest_revision_id"],
        ],
    )
