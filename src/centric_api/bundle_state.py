from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError
from .store import connect_readonly, table_exists


@dataclass(frozen=True)
class BundleComparison:
    from_run: dict[str, Any]
    to_run: dict[str, Any]
    summary: dict[str, int]
    items: tuple[dict[str, Any], ...]


def list_bundle_runs(
    db_path: Path,
    *,
    bundle_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "bundle_runs"):
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if bundle_name:
            clauses.append("bundle_name = ?")
            params.append(bundle_name)
        clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT run_id, bundle_name, download_job, started_at, finished_at,
                   zip_path, item_count, added_count, changed_count, renamed_count,
                   removed_count, unchanged_count, missing_count, dry_run
            FROM bundle_runs
            {clause}
            ORDER BY finished_at DESC, run_id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def get_bundle_run(db_path: Path, run_id: str) -> dict[str, Any] | None:
    if not db_path.is_file():
        return None
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "bundle_runs"):
            return None
        row = conn.execute(
            """
            SELECT run_id, bundle_name, download_job, started_at, finished_at,
                   manifest_path, changelog_json_path, changelog_md_path, zip_path,
                   item_count, added_count, changed_count, renamed_count,
                   removed_count, unchanged_count, missing_count, dry_run
            FROM bundle_runs
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()
    return dict(row) if row is not None else None


def list_bundle_items(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "bundle_items"):
            return []
        rows = conn.execute(
            """
            SELECT archive_path, identity, source_endpoint, source_record_id,
                   source_label, document_id, revision_id, file_path, sha256,
                   bytes, status, change_type, previous_archive_path,
                   previous_revision_id, previous_sha256, created_at
            FROM bundle_items
            WHERE run_id = ?
            ORDER BY archive_path
            """,
            [run_id],
        ).fetchall()
    return [dict(row) for row in rows]


def compare_bundle_runs(
    db_path: Path,
    *,
    from_run_id: str,
    to_run_id: str | None = None,
) -> BundleComparison:
    from_run = get_bundle_run(db_path, from_run_id)
    if from_run is None:
        raise ConfigError(f"Unknown bundle run id: {from_run_id}. Run centric-api bundle list.")
    if to_run_id is None or to_run_id == "latest":
        to_run = _latest_bundle_run_after(db_path, from_run)
        if to_run is None:
            raise ConfigError(f"No later bundle run found for {from_run['bundle_name']!r}.")
    else:
        to_run = get_bundle_run(db_path, to_run_id)
        if to_run is None:
            raise ConfigError(f"Unknown bundle run id: {to_run_id}. Run centric-api bundle list.")
        if to_run["bundle_name"] != from_run["bundle_name"]:
            raise ConfigError(
                "Bundle changelog comparison requires runs from the same bundle: "
                f"{from_run['bundle_name']} != {to_run['bundle_name']}."
            )
        if _bundle_run_order_key(to_run) < _bundle_run_order_key(from_run):
            raise ConfigError(f"Bundle changelog target {to_run_id} is older than {from_run_id}.")
    from_items = _included_items_by_identity(list_bundle_items(db_path, str(from_run["run_id"])))
    to_items = _included_items_by_identity(list_bundle_items(db_path, str(to_run["run_id"])))
    items = _compare_bundle_items(from_items, to_items)
    return BundleComparison(
        from_run=from_run,
        to_run=to_run,
        summary=change_counts(items),
        items=tuple(items),
    )


def load_download_current_rows(conn: sqlite3.Connection, download_job: str) -> list[sqlite3.Row]:
    if not table_exists(conn, "download_current"):
        return []
    return conn.execute(
        """
        SELECT document_id, revision_id, document_name, file_path, sha256, bytes,
               source_refs_json
        FROM download_current
        WHERE job_name = ? AND status = 'current'
        ORDER BY document_id, revision_id
        """,
        [download_job],
    ).fetchall()


def load_bundle_current(
    conn: sqlite3.Connection,
    bundle_name: str,
) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "bundle_current"):
        return {}
    rows = conn.execute(
        """
        SELECT identity, archive_path, source_endpoint, source_record_id, source_label,
               document_id, revision_id, file_path, sha256, bytes, last_run_id
        FROM bundle_current
        WHERE bundle_name = ?
        ORDER BY archive_path
        """,
        [bundle_name],
    ).fetchall()
    return {str(row["identity"]): dict(row) for row in rows}


def record_bundle_run(
    conn: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
    manifest_path: Path,
    changelog_json_path: Path,
    changelog_md_path: Path,
    zip_path: Path | None,
) -> None:
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
            manifest["run_id"],
            manifest["bundle"],
            manifest["download_job"],
            manifest["started_at"],
            manifest["finished_at"],
            str(manifest_path),
            str(changelog_json_path),
            str(changelog_md_path),
            str(zip_path) if zip_path else None,
            manifest["item_count"],
            manifest["added_count"],
            manifest["changed_count"],
            manifest["renamed_count"],
            manifest["removed_count"],
            manifest["unchanged_count"],
            manifest["missing_count"],
            int(bool(manifest["dry_run"])),
        ],
    )
    rows = []
    for item in items:
        rows.append(
            [
                manifest["run_id"],
                manifest["bundle"],
                item["archive_path"],
                item["source_endpoint"],
                item["source_record_id"],
                item["identity"],
                item["source_label"],
                item["document_id"],
                item["revision_id"],
                item.get("source_file_path"),
                item.get("sha256"),
                item.get("bytes"),
                item["status"],
                item["change_type"],
                item.get("previous_archive_path"),
                item.get("previous_revision_id"),
                item.get("previous_sha256"),
                manifest["finished_at"],
            ]
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO bundle_items (
                run_id, bundle_name, archive_path, source_endpoint, source_record_id,
                identity, source_label, document_id, revision_id, file_path, sha256,
                bytes, status, change_type, previous_archive_path,
                previous_revision_id, previous_sha256, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def replace_bundle_current(
    conn: sqlite3.Connection,
    *,
    bundle_name: str,
    run_id: str,
    items: list[dict[str, Any]],
    selected_at: str,
) -> None:
    conn.execute("DELETE FROM bundle_current WHERE bundle_name = ?", [bundle_name])
    rows = [
        [
            bundle_name,
            item["archive_path"],
            item["identity"],
            item["source_endpoint"],
            item["source_record_id"],
            item["source_label"],
            item["document_id"],
            item["revision_id"],
            item["source_file_path"],
            item["sha256"],
            item["bytes"],
            run_id,
            selected_at,
        ]
        for item in items
        if item["status"] == "included"
    ]
    if rows:
        conn.executemany(
            """
            INSERT INTO bundle_current (
                bundle_name, archive_path, identity, source_endpoint, source_record_id,
                source_label, document_id, revision_id, file_path, sha256, bytes,
                last_run_id, selected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def bundle_run_exists(conn: sqlite3.Connection, run_id: str) -> bool:
    if not table_exists(conn, "bundle_runs"):
        return False
    row = conn.execute("SELECT 1 FROM bundle_runs WHERE run_id = ?", [run_id]).fetchone()
    return row is not None


def change_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "added_count": sum(1 for item in items if item["change_type"] == "added"),
        "changed_count": sum(1 for item in items if item["change_type"] == "changed"),
        "renamed_count": sum(1 for item in items if item["change_type"] == "renamed"),
        "removed_count": sum(1 for item in items if item["change_type"] == "removed"),
        "unchanged_count": sum(1 for item in items if item["change_type"] == "unchanged"),
        "missing_count": sum(1 for item in items if item["status"] == "missing_download"),
    }


def previous_run_id(previous: dict[str, dict[str, Any]]) -> str | None:
    run_ids = sorted(
        {str(item["last_run_id"]) for item in previous.values() if item["last_run_id"]}
    )
    return run_ids[-1] if run_ids else None


def _latest_bundle_run_after(db_path: Path, from_run: dict[str, Any]) -> dict[str, Any] | None:
    with connect_readonly(db_path) as conn:
        if not table_exists(conn, "bundle_runs"):
            return None
        row = conn.execute(
            """
            SELECT run_id, bundle_name, download_job, started_at, finished_at,
                   manifest_path, changelog_json_path, changelog_md_path, zip_path,
                   item_count, added_count, changed_count, renamed_count,
                   removed_count, unchanged_count, missing_count, dry_run
            FROM bundle_runs
            WHERE bundle_name = ?
              AND (finished_at > ? OR (finished_at = ? AND run_id > ?))
            ORDER BY finished_at DESC, run_id DESC
            LIMIT 1
            """,
            [
                from_run["bundle_name"],
                from_run["finished_at"],
                from_run["finished_at"],
                from_run["run_id"],
            ],
        ).fetchone()
    return dict(row) if row is not None else None


def _included_items_by_identity(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["identity"]): item for item in items if item["status"] == "included"}


def _bundle_run_order_key(run: dict[str, Any]) -> tuple[str, str]:
    return (str(run["finished_at"]), str(run["run_id"]))


def _compare_bundle_items(
    from_items: dict[str, dict[str, Any]],
    to_items: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    compared: list[dict[str, Any]] = []
    for identity in sorted(set(from_items) | set(to_items)):
        previous = from_items.get(identity)
        current = to_items.get(identity)
        if previous is None and current is not None:
            change_type = "added"
        elif previous is not None and current is None:
            change_type = "removed"
        elif (
            previous
            and current
            and (
                previous["revision_id"] != current["revision_id"]
                or previous["sha256"] != current["sha256"]
            )
        ):
            change_type = "changed"
        elif previous and current and previous["archive_path"] != current["archive_path"]:
            change_type = "renamed"
        else:
            change_type = "unchanged"
        item = current or previous
        if item is None:
            continue
        compared.append(
            {
                **item,
                "status": "included" if current is not None else "removed",
                "change_type": change_type,
                "previous_archive_path": previous["archive_path"] if previous else None,
                "previous_revision_id": previous["revision_id"] if previous else None,
                "previous_sha256": previous["sha256"] if previous else None,
            }
        )
    return compared
