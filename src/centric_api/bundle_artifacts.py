from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any


def manifest_item(item: dict[str, Any]) -> dict[str, Any]:
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


def build_bundle_changelog(
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


def write_bundle_artifacts(
    *,
    temp_run_dir: Path,
    temp_zip_path: Path | None,
    manifest: dict[str, Any],
    changelog: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    shutil.rmtree(temp_run_dir, ignore_errors=True)
    if temp_zip_path is not None:
        temp_zip_path.unlink(missing_ok=True)
    write_json(temp_run_dir / "manifest.json", manifest)
    write_json(temp_run_dir / "changelog.json", changelog)
    write_text(temp_run_dir / "changelog.md", render_changelog_md(changelog))
    copy_bundle_files(items)
    if temp_zip_path is not None:
        write_zip(temp_zip_path, temp_run_dir)


def cleanup_bundle_artifacts(
    *,
    temp_run_dir: Path,
    temp_zip_path: Path | None,
    run_dir: Path,
    zip_path: Path | None,
    final_run_dir_created: bool,
    final_zip_created: bool,
) -> None:
    shutil.rmtree(temp_run_dir, ignore_errors=True)
    if temp_zip_path is not None:
        temp_zip_path.unlink(missing_ok=True)
    if final_run_dir_created:
        shutil.rmtree(run_dir, ignore_errors=True)
    if final_zip_created and zip_path is not None:
        zip_path.unlink(missing_ok=True)


def copy_bundle_files(items: list[dict[str, Any]]) -> None:
    for item in items:
        if item["status"] != "included":
            continue
        target_path = Path(str(item["target_path"]))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(item["source_file_path"]), target_path)
        _verify_copied_file(item, target_path)


def write_zip(zip_path: Path, run_dir: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = zip_path.parent / f".{zip_path.name}.tmp"
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir).as_posix())
    temp_path.replace(zip_path)


def _verify_copied_file(item: dict[str, Any], target_path: Path) -> None:
    expected_bytes = item.get("bytes")
    expected_sha256 = item.get("sha256")
    if expected_bytes is not None and target_path.stat().st_size != int(expected_bytes):
        raise RuntimeError(f"copied bundle file size mismatch: {item['archive_path']}")
    if expected_sha256 is not None and _sha256(target_path) != str(expected_sha256):
        raise RuntimeError(f"copied bundle file hash mismatch: {item['archive_path']}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_changelog_md(changelog: dict[str, Any]) -> str:
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
                lines.append(f"  Previous path: {item.get('previous_archive_path') or 'unknown'}")
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.tmp"
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.tmp"
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)
