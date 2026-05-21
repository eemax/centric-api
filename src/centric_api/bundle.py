from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .bundle_artifacts import (
    build_bundle_changelog,
    cleanup_bundle_artifacts,
    manifest_item,
    write_bundle_artifacts,
)
from .bundle_config import (
    BundleConfig,
    BundleJob,
    BundleLayout,
    select_bundle_job,
)
from .bundle_state import (
    bundle_run_exists,
    change_counts,
    load_bundle_current,
    load_download_current_rows,
    previous_run_id,
    record_bundle_run,
    replace_bundle_current,
)
from .config import ConfigError
from .db_schema import ensure_bundle_tables, ensure_download_tables
from .store import connect, connect_readonly


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


def run_bundle_job(
    *,
    db_path: Path,
    config: BundleConfig,
    job_name: str | None = None,
    dry_run: bool = False,
    zip_bundle: bool = True,
) -> BundleRunResult:
    job = select_bundle_job(config, job_name)
    created_at = datetime.now(UTC)

    with _connect_for_bundle(db_path, dry_run=dry_run) as conn:
        if not dry_run:
            ensure_bundle_tables(conn)
            ensure_download_tables(conn)
        run_id = _allocate_run_id(config.output_dir, conn, created_at, job.name)
        run_dir = config.output_dir / "runs" / run_id
        temp_run_dir = config.output_dir / "runs" / f".{run_id}.tmp"
        files_dir = temp_run_dir / "files"
        manifest_path = run_dir / "manifest.json"
        changelog_json_path = run_dir / "changelog.json"
        changelog_md_path = run_dir / "changelog.md"
        zip_path = config.output_dir / f"{run_id}.zip" if zip_bundle and not dry_run else None
        temp_zip_path = (
            config.output_dir / f".{run_id}.zip.tmp" if zip_bundle and not dry_run else None
        )
        rows = load_download_current_rows(conn, job.download_job)
        items = _build_bundle_items(conn, rows, job=job, files_dir=files_dir)
        previous = load_bundle_current(conn, job.name)

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
    counts = change_counts(all_items)
    manifest_items = [manifest_item(item) for item in all_items]

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
    changelog = build_bundle_changelog(manifest, previous_run_id=previous_run_id(previous))

    if not dry_run:
        final_run_dir_created = False
        final_zip_created = False
        try:
            write_bundle_artifacts(
                temp_run_dir=temp_run_dir,
                temp_zip_path=temp_zip_path,
                manifest=manifest,
                changelog=changelog,
                items=items,
            )
            temp_run_dir.replace(run_dir)
            final_run_dir_created = True
            if temp_zip_path is not None and zip_path is not None:
                temp_zip_path.replace(zip_path)
                final_zip_created = True
            with connect(db_path) as conn:
                ensure_bundle_tables(conn)
                record_bundle_run(
                    conn,
                    manifest=manifest,
                    items=all_items,
                    manifest_path=manifest_path,
                    changelog_json_path=changelog_json_path,
                    changelog_md_path=changelog_md_path,
                    zip_path=zip_path,
                )
                replace_bundle_current(
                    conn,
                    bundle_name=job.name,
                    run_id=run_id,
                    items=items,
                    selected_at=_datetime_to_db(datetime.now(UTC)),
                )
        except Exception:
            cleanup_bundle_artifacts(
                temp_run_dir=temp_run_dir,
                temp_zip_path=temp_zip_path,
                run_dir=run_dir,
                zip_path=zip_path,
                final_run_dir_created=final_run_dir_created,
                final_zip_created=final_zip_created,
            )
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


def _connect_for_bundle(db_path: Path, *, dry_run: bool) -> sqlite3.Connection:
    return connect_readonly(db_path) if dry_run else connect(db_path)


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
        size = (
            int(row["bytes"])
            if row["bytes"] is not None
            else (source_path.stat().st_size if file_exists else None)
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
    values = [_string_value(_extract_path(payload, field)) for field in rule.fields]
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


def _allocate_run_id(
    output_dir: Path,
    conn: sqlite3.Connection,
    created_at: datetime,
    bundle_name: str,
) -> str:
    base = f"{created_at:%Y-%m-%dT%H%M%SZ}-{_safe_path_part(bundle_name)}"
    runs_dir = output_dir / "runs"
    for index in range(100):
        suffix = "" if index == 0 else f"-{index + 1}"
        run_id = f"{base}{suffix}"
        if (runs_dir / run_id).exists() or (output_dir / f"{run_id}.zip").exists():
            continue
        if bundle_run_exists(conn, run_id):
            continue
        return run_id
    raise RuntimeError("Could not allocate bundle run id.")
