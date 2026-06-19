from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..changelog import ChangelogRun, record_changelog, seed_changelog_index
from ..config import ConfigError, runtime_path
from ..defaults import db_path
from ..schema import load_endpoint_schemas
from ..store import connect, ingest_raw_dir
from .health import _changelog_record, _ingest_record


def run_rebuild_db(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ConfigError("rebuild-db is destructive; rerun with --yes to rebuild SQLite.")
    target_db_path = db_path(args.db)
    raw_dir = Path(args.raw_dir).expanduser() if args.raw_dir else runtime_path("raw")
    if not raw_dir.exists():
        raise ConfigError(f"Raw evidence directory not found: {raw_dir}")
    progress: ProgressCallback | None = None if args.json else print
    _emit_progress(progress, "Rebuilding SQLite...")
    _emit_progress(progress, f"DB:  {target_db_path}")
    _emit_progress(progress, f"Raw: {raw_dir}")
    _emit_progress(progress, "")

    _emit_progress(progress, "Loading endpoint schemas...")
    schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
    temp_db_path = _temp_rebuild_db_path(target_db_path)
    _remove_db_files(temp_db_path)
    try:
        _emit_progress(progress, f"Temp DB: {temp_db_path}")
        _emit_progress(progress, "Ingesting raw records...")
        ingest_result = ingest_raw_dir(raw_dir, temp_db_path, schemas=schemas)
        changelog_run: ChangelogRun | None = None
        changelog_index_seed: ChangelogRun | None = None
        changelog_skipped = None
        if args.skip_changelog:
            changelog_skipped = (
                "full changelog event rebuild skipped; compact changelog index seeded"
            )
            _emit_progress(progress, "Skipping changelog event rebuild...")
            _emit_progress(progress, "Seeding changelog index...")
            changelog_index_seed = seed_changelog_index(
                temp_db_path,
                progress=_indented_progress(progress),
            )
        else:
            _emit_progress(progress, "Updating changelog...")
            changelog_run = record_changelog(
                temp_db_path,
                full=True,
                progress=_indented_progress(progress),
            )
        _emit_progress(progress, "Opening rebuilt DB...")
        _checkpoint_rebuilt_db(temp_db_path)
        _emit_progress(progress, "Backing up existing DB files...")
        backups = _copy_existing_db_files(target_db_path)
        _emit_progress(progress, "Promoting rebuilt DB...")
        _promote_rebuilt_db(temp_db_path, target_db_path)
    except Exception:
        _remove_db_files(temp_db_path)
        raise
    payload = {
        "db": str(target_db_path),
        "raw_dir": str(raw_dir),
        "backups": [str(path) for path in backups],
        "ingest": _ingest_record(ingest_result),
        "changelog": _changelog_record(changelog_run) if changelog_run is not None else None,
        "changelog_index": (
            _changelog_record(changelog_index_seed) if changelog_index_seed is not None else None
        ),
        "changelog_skipped": changelog_skipped,
    }
    if args.json:
        print(json.dumps(payload, default=str))
    else:
        print()
        print("SQLite Rebuilt")
        print()
        print(f"DB:      {target_db_path}")
        print(f"Raw:     {raw_dir}")
        print(f"Backups: {', '.join(payload['backups']) if backups else 'none'}")
        print()
        print("Ingest")
        print(f"Files:   {ingest_result.applied_files} applied")
        print(f"Records: {ingest_result.records_read} read")
        print(f"Upserts: {ingest_result.records_upserted}")
        print(f"Deletes: {ingest_result.records_deleted}")
        print(f"Hard del: {ingest_result.records_hard_deleted}")
        print()
        print("Changelog")
        if changelog_run is not None:
            print(f"Run:     {changelog_run.run_id}")
            print(f"Events:  {changelog_run.event_count}")
        else:
            print("Events:  skipped")
            if changelog_index_seed is not None:
                print(f"Index:   seeded ({changelog_index_seed.record_count} records)")
            print("Note:    event history was not rebuilt.")
    return 0


ProgressCallback = Callable[[str], None]


def _emit_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _indented_progress(progress: ProgressCallback | None) -> ProgressCallback | None:
    if progress is None:
        return None

    def emit(message: str) -> None:
        progress(f"  {message}")

    return emit


def _temp_rebuild_db_path(target_db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return target_db_path.with_name(f".{target_db_path.name}.rebuild-{timestamp}.tmp")


def _db_files(path: Path) -> tuple[Path, Path, Path]:
    return path, Path(f"{path}-wal"), Path(f"{path}-shm")


def _remove_db_files(path: Path) -> None:
    for db_file in _db_files(path):
        db_file.unlink(missing_ok=True)


def _copy_existing_db_files(target_db_path: Path) -> list[Path]:
    backups: list[Path] = []
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    for path in _db_files(target_db_path):
        if not path.exists():
            continue
        backup = path.with_name(f"{path.name}.backup-{timestamp}")
        shutil.copy2(str(path), str(backup))
        backups.append(backup)
    return backups


def _checkpoint_rebuilt_db(temp_db_path: Path) -> None:
    with connect(temp_db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()


def _promote_rebuilt_db(temp_db_path: Path, target_db_path: Path) -> None:
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_db_path.replace(target_db_path)
    for sidecar in _db_files(target_db_path)[1:]:
        sidecar.unlink(missing_ok=True)
    for sidecar in _db_files(temp_db_path)[1:]:
        sidecar.unlink(missing_ok=True)


__all__ = ["run_rebuild_db"]
