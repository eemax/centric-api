from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, runtime_home, runtime_path
from .db_schema import ensure_bundle_tables, ensure_download_tables
from .store import connect, connect_readonly, table_exists

DEFAULT_BUNDLE_CONFIG_PATH = Path("config/bundle.yml")
PRIVATE_BUNDLE_CONFIG_PATH = Path("bundle.yml")
DEFAULT_BUNDLE_DIR = Path("bundles")
DEFAULT_SOURCE_LABEL_FIELDS = ("node_name",)
ROOT_CONFIG_KEYS = {"version", "output_dir", "bundles"}
BUNDLE_CONFIG_KEYS = {"name", "download_job", "layout"}
LAYOUT_CONFIG_KEYS = {"source_label"}
SOURCE_LABEL_CONFIG_KEYS = {"fields", "join"}


@dataclass(frozen=True)
class SourceLabelRule:
    fields: tuple[str, ...] = DEFAULT_SOURCE_LABEL_FIELDS
    join: str = " - "


@dataclass(frozen=True)
class BundleLayout:
    source_label_rules: dict[str, SourceLabelRule] = field(
        default_factory=lambda: {"default": SourceLabelRule()}
    )


@dataclass(frozen=True)
class BundleJob:
    name: str
    download_job: str
    layout: BundleLayout = field(default_factory=BundleLayout)


@dataclass(frozen=True)
class BundleConfig:
    path: Path
    bundles: tuple[BundleJob, ...]
    output_dir: Path = field(default_factory=lambda: runtime_path(DEFAULT_BUNDLE_DIR))


@dataclass(frozen=True)
class BundleRunResult:
    run_id: str
    bundle_name: str
    download_job: str
    manifest_path: Path
    changelog_json_path: Path
    changelog_md_path: Path
    zip_path: Path | None
    item_count: int
    added_count: int
    changed_count: int
    renamed_count: int
    removed_count: int
    unchanged_count: int
    missing_count: int
    dry_run: bool


@dataclass(frozen=True)
class BundleComparison:
    from_run: dict[str, Any]
    to_run: dict[str, Any]
    summary: dict[str, int]
    items: tuple[dict[str, Any], ...]


def resolve_bundle_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    private_path = runtime_home() / PRIVATE_BUNDLE_CONFIG_PATH
    if private_path.is_file():
        return private_path
    return DEFAULT_BUNDLE_CONFIG_PATH


def load_bundle_config(path: str | Path | None = None) -> BundleConfig:
    config_path = resolve_bundle_config_path(path)
    payload = _load_payload(config_path)
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "bundle config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("bundle config version must be 1.")
    output_dir = _runtime_output_dir(payload.get("output_dir"))
    bundles_raw = _list(payload.get("bundles"), "bundles")
    bundles = tuple(_parse_bundle(raw, index) for index, raw in enumerate(bundles_raw))
    if not bundles:
        raise ConfigError("bundle config must contain at least one bundle.")
    _ensure_unique_bundle_names(bundles)
    return BundleConfig(path=config_path, bundles=bundles, output_dir=output_dir)


def run_bundle_job(
    *,
    db_path: Path,
    config: BundleConfig,
    job_name: str | None = None,
    dry_run: bool = False,
    zip_bundle: bool = True,
) -> BundleRunResult:
    job = _select_bundle(config, job_name)
    created_at = datetime.now(UTC)
    run_id = _allocate_run_id(config.output_dir, created_at, job.name)
    run_dir = config.output_dir / "runs" / run_id
    temp_run_dir = config.output_dir / "runs" / f".{run_id}.tmp"
    files_dir = temp_run_dir / "files"
    manifest_path = run_dir / "manifest.json"
    changelog_json_path = run_dir / "changelog.json"
    changelog_md_path = run_dir / "changelog.md"
    zip_path = config.output_dir / f"{run_id}.zip" if zip_bundle and not dry_run else None
    temp_zip_path = (
        config.output_dir / f".{run_id}.zip.tmp"
        if zip_bundle and not dry_run
        else None
    )

    with _connect_for_bundle(db_path, dry_run=dry_run) as conn:
        if not dry_run:
            ensure_bundle_tables(conn)
            ensure_download_tables(conn)
        rows = _load_download_current_rows(conn, job.download_job)
        items = _build_bundle_items(conn, rows, job=job, files_dir=files_dir)
        previous = _load_bundle_current(conn, job.name)

    if not rows:
        raise ConfigError(
            f"Bundle job {job.name!r} found no current downloads for "
            f"download job {job.download_job!r}."
        )
    if not items:
        raise ConfigError(
            f"Bundle job {job.name!r} could not build any bundle items from "
            f"download job {job.download_job!r}."
        )
    missing = [item for item in items if item["status"] == "missing_download"]
    if missing:
        first = missing[0]
        raise ConfigError(
            f"Bundle job {job.name!r} is missing downloaded files; first missing: "
            f"{first['document_id']} at {first['source_file_path']}"
        )

    items = _apply_bundle_changes(items, previous)
    removed_items = _removed_bundle_items(previous, items)
    all_items = [*items, *removed_items]
    counts = _change_counts(all_items)
    manifest_items = [_manifest_item(item) for item in all_items]

    manifest = {
        "run_id": run_id,
        "bundle": job.name,
        "download_job": job.download_job,
        "dry_run": dry_run,
        "zip": zip_path.name if zip_path else None,
        "started_at": _datetime_to_db(created_at),
        "finished_at": _datetime_to_db(datetime.now(UTC)),
        "item_count": len(items),
        **counts,
        "items": manifest_items,
    }
    changelog = _bundle_changelog(manifest, previous_run_id=_previous_run_id(previous))

    if not dry_run:
        final_run_dir_created = False
        final_zip_created = False
        try:
            _write_json(temp_run_dir / "manifest.json", manifest)
            _write_json(temp_run_dir / "changelog.json", changelog)
            _write_text(temp_run_dir / "changelog.md", _render_changelog_md(changelog))
            _copy_bundle_files(items)
            if temp_zip_path is not None:
                _write_zip(temp_zip_path, temp_run_dir)
            temp_run_dir.replace(run_dir)
            final_run_dir_created = True
            if temp_zip_path is not None and zip_path is not None:
                temp_zip_path.replace(zip_path)
                final_zip_created = True
            with connect(db_path) as conn:
                ensure_bundle_tables(conn)
                _record_bundle_run(
                    conn,
                    manifest=manifest,
                    items=all_items,
                    manifest_path=manifest_path,
                    changelog_json_path=changelog_json_path,
                    changelog_md_path=changelog_md_path,
                    zip_path=zip_path,
                )
                _replace_bundle_current(conn, bundle_name=job.name, run_id=run_id, items=items)
        except Exception:
            shutil.rmtree(temp_run_dir, ignore_errors=True)
            if temp_zip_path is not None:
                temp_zip_path.unlink(missing_ok=True)
            if final_run_dir_created:
                shutil.rmtree(run_dir, ignore_errors=True)
            if final_zip_created and zip_path is not None:
                zip_path.unlink(missing_ok=True)
            raise

    return BundleRunResult(
        run_id=run_id,
        bundle_name=job.name,
        download_job=job.download_job,
        manifest_path=manifest_path,
        changelog_json_path=changelog_json_path,
        changelog_md_path=changelog_md_path,
        zip_path=zip_path,
        item_count=len(items),
        added_count=counts["added_count"],
        changed_count=counts["changed_count"],
        renamed_count=counts["renamed_count"],
        removed_count=counts["removed_count"],
        unchanged_count=counts["unchanged_count"],
        missing_count=counts["missing_count"],
        dry_run=dry_run,
    )


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
            raise ConfigError(
                f"Bundle changelog target {to_run_id} is older than {from_run_id}."
            )
    from_items = _included_items_by_identity(list_bundle_items(db_path, str(from_run["run_id"])))
    to_items = _included_items_by_identity(list_bundle_items(db_path, str(to_run["run_id"])))
    items = _compare_bundle_items(from_items, to_items)
    return BundleComparison(
        from_run=from_run,
        to_run=to_run,
        summary=_change_counts(items),
        items=tuple(items),
    )


def _connect_for_bundle(db_path: Path, *, dry_run: bool) -> sqlite3.Connection:
    return connect_readonly(db_path) if dry_run else connect(db_path)


def _load_download_current_rows(conn: sqlite3.Connection, download_job: str) -> list[sqlite3.Row]:
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


def _build_bundle_items(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    job: BundleJob,
    files_dir: Path,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    used_paths: set[str] = set()
    for row in rows:
        source_path = Path(str(row["file_path"] or ""))
        file_exists = source_path.is_file()
        sha256 = str(row["sha256"] or _sha256(source_path)) if file_exists else None
        size = int(row["bytes"]) if row["bytes"] is not None else (
            source_path.stat().st_size if file_exists else None
        )
        filename = source_path.name or str(row["document_name"] or row["document_id"])
        source_refs = _json_list(row["source_refs_json"])
        for ref in source_refs:
            source_endpoint = str(ref.get("endpoint") or "")
            source_record_id = str(ref.get("record_id") or "")
            source_payload = _load_endpoint_payload(
                conn,
                endpoint=source_endpoint,
                record_id=source_record_id,
            )
            source_label = _source_label(
                job.layout,
                endpoint=source_endpoint,
                record_id=source_record_id,
                payload=source_payload,
            )
            archive_path = _unique_archive_path(
                used_paths,
                Path("files")
                / _safe_path_part(source_endpoint)
                / _safe_path_part(source_label)
                / _safe_filename(filename),
            )
            target_path = files_dir / Path(archive_path).relative_to("files")
            items.append(
                {
                    "identity": _bundle_identity(
                        source_endpoint=source_endpoint,
                        source_record_id=source_record_id,
                        document_id=str(row["document_id"]),
                    ),
                    "archive_path": archive_path,
                    "target_path": str(target_path),
                    "source_file_path": str(source_path),
                    "source_endpoint": source_endpoint,
                    "source_record_id": source_record_id,
                    "source_label": source_label,
                    "document_id": str(row["document_id"]),
                    "revision_id": str(row["revision_id"]),
                    "document_name": row["document_name"],
                    "sha256": sha256,
                    "bytes": size,
                    "status": "included" if file_exists else "missing_download",
                }
            )
    return items


def _apply_bundle_changes(
    items: list[dict[str, Any]],
    previous: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for item in items:
        prior = previous.get(item["identity"])
        if prior is None:
            change_type = "added"
        elif prior["revision_id"] != item["revision_id"] or prior["sha256"] != item["sha256"]:
            change_type = "changed"
        elif prior["archive_path"] != item["archive_path"]:
            change_type = "renamed"
        else:
            change_type = "unchanged"
        changed.append(
            {
                **item,
                "change_type": change_type,
                "previous_archive_path": prior["archive_path"] if prior else None,
                "previous_revision_id": prior["revision_id"] if prior else None,
                "previous_sha256": prior["sha256"] if prior else None,
                "previous_run_id": prior["last_run_id"] if prior else None,
            }
        )
    return changed


def _removed_bundle_items(
    previous: dict[str, dict[str, Any]],
    current_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_identities = {item["identity"] for item in current_items}
    removed: list[dict[str, Any]] = []
    for identity, prior in sorted(previous.items()):
        if identity in current_identities:
            continue
        removed.append(
            {
                "identity": identity,
                "archive_path": prior["archive_path"],
                "target_path": None,
                "source_file_path": prior["file_path"],
                "source_endpoint": prior["source_endpoint"],
                "source_record_id": prior["source_record_id"],
                "source_label": prior["source_label"],
                "document_id": prior["document_id"],
                "revision_id": prior["revision_id"],
                "document_name": None,
                "sha256": prior["sha256"],
                "bytes": prior["bytes"],
                "status": "removed",
                "change_type": "removed",
                "previous_archive_path": prior["archive_path"],
                "previous_revision_id": prior["revision_id"],
                "previous_sha256": prior["sha256"],
                "previous_run_id": prior["last_run_id"],
            }
        )
    return removed


def _load_bundle_current(
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
        elif previous and current and (
            previous["revision_id"] != current["revision_id"]
            or previous["sha256"] != current["sha256"]
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


def _record_bundle_run(
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


def _manifest_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_path": item["archive_path"],
        "previous_archive_path": item.get("previous_archive_path"),
        "source_endpoint": item["source_endpoint"],
        "source_record_id": item["source_record_id"],
        "source_label": item["source_label"],
        "document_id": item["document_id"],
        "revision_id": item["revision_id"],
        "document_name": item.get("document_name"),
        "sha256": item.get("sha256"),
        "bytes": item.get("bytes"),
        "status": item["status"],
        "change_type": item["change_type"],
        "previous_revision_id": item.get("previous_revision_id"),
        "previous_sha256": item.get("previous_sha256"),
        "previous_run_id": item.get("previous_run_id"),
    }


def _replace_bundle_current(
    conn: sqlite3.Connection,
    *,
    bundle_name: str,
    run_id: str,
    items: list[dict[str, Any]],
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
            _datetime_to_db(datetime.now(UTC)),
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


def _copy_bundle_files(items: list[dict[str, Any]]) -> None:
    for item in items:
        if item["status"] != "included":
            continue
        target_path = Path(str(item["target_path"]))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(item["source_file_path"]), target_path)


def _write_zip(zip_path: Path, run_dir: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = zip_path.parent / f".{zip_path.name}.tmp"
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir).as_posix())
    temp_path.replace(zip_path)


def _bundle_changelog(
    manifest: dict[str, Any],
    *,
    previous_run_id: str | None,
) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "bundle": manifest["bundle"],
        "download_job": manifest["download_job"],
        "previous_run_id": previous_run_id,
        "created_at": manifest["finished_at"],
        "summary": {
            "added": manifest["added_count"],
            "changed": manifest["changed_count"],
            "renamed": manifest["renamed_count"],
            "removed": manifest["removed_count"],
            "unchanged": manifest["unchanged_count"],
            "missing": manifest["missing_count"],
        },
        "items": [
            {
                "change_type": item["change_type"],
                "archive_path": item["archive_path"],
                "previous_archive_path": item.get("previous_archive_path"),
                "source_endpoint": item["source_endpoint"],
                "source_record_id": item["source_record_id"],
                "source_label": item["source_label"],
                "document_id": item["document_id"],
                "revision_id": item["revision_id"],
                "previous_revision_id": item.get("previous_revision_id"),
                "sha256": item.get("sha256"),
                "previous_sha256": item.get("previous_sha256"),
            }
            for item in manifest["items"]
            if item["change_type"] != "unchanged"
        ],
    }


def _render_changelog_md(changelog: dict[str, Any]) -> str:
    summary = changelog["summary"]
    lines = [
        f"# {changelog['bundle']}",
        "",
        f"Bundle run: {changelog['run_id']}",
        f"Previous run: {changelog['previous_run_id'] or 'none'}",
        "",
        "## Summary",
        "",
        f"- Added: {summary['added']}",
        f"- Changed: {summary['changed']}",
        f"- Renamed: {summary['renamed']}",
        f"- Removed: {summary['removed']}",
        f"- Unchanged: {summary['unchanged']}",
        f"- Missing: {summary['missing']}",
    ]
    grouped = {
        "added": [],
        "changed": [],
        "renamed": [],
        "removed": [],
        "missing": [],
    }
    for item in changelog["items"]:
        grouped.setdefault(item["change_type"], []).append(item)
    for change_type, title in (
        ("added", "Added"),
        ("changed", "Changed"),
        ("renamed", "Renamed"),
        ("removed", "Removed"),
        ("missing", "Missing"),
    ):
        if not grouped.get(change_type):
            continue
        lines.extend(["", f"## {title}", ""])
        for item in grouped[change_type]:
            lines.append(f"- {item['archive_path']}")
            if change_type == "changed":
                lines.append(
                    f"  Previous revision: {item.get('previous_revision_id') or 'unknown'}"
                )
                lines.append(f"  Current revision: {item['revision_id']}")
            elif change_type == "renamed":
                lines.append(
                    f"  Previous path: {item.get('previous_archive_path') or 'unknown'}"
                )
    lines.append("")
    return "\n".join(lines)


def _change_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "added_count": sum(1 for item in items if item["change_type"] == "added"),
        "changed_count": sum(1 for item in items if item["change_type"] == "changed"),
        "renamed_count": sum(1 for item in items if item["change_type"] == "renamed"),
        "removed_count": sum(1 for item in items if item["change_type"] == "removed"),
        "unchanged_count": sum(1 for item in items if item["change_type"] == "unchanged"),
        "missing_count": sum(1 for item in items if item["status"] == "missing_download"),
    }


def _previous_run_id(previous: dict[str, dict[str, Any]]) -> str | None:
    run_ids = sorted(
        {str(item["last_run_id"]) for item in previous.values() if item["last_run_id"]}
    )
    return run_ids[-1] if run_ids else None


def _load_endpoint_payload(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    record_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT payload_json
        FROM endpoint_records
        WHERE endpoint = ? AND record_id = ?
        """,
        [endpoint, record_id],
    ).fetchone()
    return _json_dict(row["payload_json"]) if row is not None else {}


def _bundle_identity(
    *,
    source_endpoint: str,
    source_record_id: str,
    document_id: str,
) -> str:
    return "\x1f".join([source_endpoint, source_record_id, document_id])


def _source_label(
    layout: BundleLayout,
    *,
    endpoint: str,
    record_id: str,
    payload: dict[str, Any],
) -> str:
    rule = layout.source_label_rules.get(endpoint) or layout.source_label_rules["default"]
    values = [
        _string_value(_extract_path(payload, field))
        for field in rule.fields
    ]
    label = rule.join.join(value for value in values if value)
    return label or record_id


def _unique_archive_path(used_paths: set[str], path: Path) -> str:
    candidate = path.as_posix()
    if candidate not in used_paths:
        used_paths.add(candidate)
        return candidate
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = (parent / f"{stem} ({index}){suffix}").as_posix()
        if candidate not in used_paths:
            used_paths.add(candidate)
            return candidate
        index += 1


def _parse_bundle(raw: Any, index: int) -> BundleJob:
    if not isinstance(raw, dict):
        raise ConfigError(f"bundle bundles[{index}] must be an object.")
    _reject_unknown_keys(raw, BUNDLE_CONFIG_KEYS, f"bundle bundles[{index}]")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"bundle bundles[{index}].name must be a non-empty string.")
    download_job = raw.get("download_job")
    if not isinstance(download_job, str) or not download_job.strip():
        raise ConfigError(f"bundle bundles[{index}].download_job must be a non-empty string.")
    return BundleJob(
        name=name.strip(),
        download_job=download_job.strip(),
        layout=_parse_layout(raw.get("layout", {}), name.strip()),
    )


def _parse_layout(raw: Any, bundle_name: str) -> BundleLayout:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"bundle[{bundle_name}].layout must be an object.")
    _reject_unknown_keys(raw, LAYOUT_CONFIG_KEYS, f"bundle[{bundle_name}].layout")
    source_label_raw = raw.get("source_label", {})
    if source_label_raw is None:
        source_label_raw = {}
    if not isinstance(source_label_raw, dict):
        raise ConfigError(f"bundle[{bundle_name}].layout.source_label must be an object.")
    rules = {"default": SourceLabelRule()}
    for endpoint, rule_raw in source_label_raw.items():
        rules[str(endpoint)] = _parse_source_label_rule(
            rule_raw,
            f"bundle[{bundle_name}].layout.source_label[{endpoint}]",
        )
    if "default" not in rules:
        rules["default"] = SourceLabelRule()
    return BundleLayout(source_label_rules=rules)


def _parse_source_label_rule(raw: Any, field_name: str) -> SourceLabelRule:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, SOURCE_LABEL_CONFIG_KEYS, field_name)
    fields = raw.get("fields", list(DEFAULT_SOURCE_LABEL_FIELDS))
    field_values = _list(fields, f"{field_name}.fields")
    parsed_fields = []
    for field_value in field_values:
        if not isinstance(field_value, str) or not field_value.strip():
            raise ConfigError(f"{field_name}.fields must contain non-empty strings.")
        parsed_fields.append(field_value.strip())
    join = raw.get("join", " - ")
    if not isinstance(join, str):
        raise ConfigError(f"{field_name}.join must be a string.")
    return SourceLabelRule(fields=tuple(parsed_fields), join=join)


def _select_bundle(config: BundleConfig, job_name: str | None) -> BundleJob:
    if job_name is None:
        return config.bundles[0]
    for job in config.bundles:
        if job.name == job_name:
            return job
    raise ConfigError(f"Unknown bundle job: {job_name}")


def _ensure_unique_bundle_names(bundles: tuple[BundleJob, ...]) -> None:
    names: set[str] = set()
    for bundle in bundles:
        if bundle.name in names:
            raise ConfigError(f"Duplicate bundle name: {bundle.name}")
        names.add(bundle.name)


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Bundle config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError("Bundle config root must be an object.")
    return payload


def _runtime_output_dir(value: Any) -> Path:
    if value is None:
        return runtime_path(DEFAULT_BUNDLE_DIR)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("bundle output_dir must be a non-empty string.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else runtime_path(path)


def _list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be an array.")
    return value


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{field_name} has unknown keys: {', '.join(unknown)}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.tmp"
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.tmp"
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _json_dict(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else {}


def _json_list(value: str | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    payload = json.loads(value)
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _extract_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_path_part(value: str) -> str:
    safe = re.sub(r"[/:\\]+", "_", value.strip())
    safe = re.sub(r"\s+", " ", safe).strip(". ")
    return safe or "unknown"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[/:\\]+", "_", value.strip())
    safe = safe.strip(". ")
    return safe or "download.bin"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _datetime_to_db(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _allocate_run_id(output_dir: Path, created_at: datetime, bundle_name: str) -> str:
    base = f"{created_at:%Y-%m-%dT%H%M%SZ}-{_safe_path_part(bundle_name)}"
    runs_dir = output_dir / "runs"
    for index in range(100):
        suffix = "" if index == 0 else f"-{index + 1}"
        run_id = f"{base}{suffix}"
        if not (runs_dir / run_id).exists() and not (output_dir / f"{run_id}.zip").exists():
            return run_id
    raise RuntimeError("Could not allocate bundle run id.")
