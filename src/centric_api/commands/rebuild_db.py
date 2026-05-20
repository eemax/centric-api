from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..changelog import record_changelog
from ..config import ConfigError, runtime_path
from ..defaults import db_path
from ..schema import load_endpoint_schemas
from ..store import connect, ingest_raw_dir
from .health import _backup_existing_db_files, _changelog_record, _ingest_record


def run_rebuild_db(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ConfigError("rebuild-db is destructive; rerun with --yes to rebuild SQLite.")
    target_db_path = db_path(args.db)
    raw_dir = Path(args.raw_dir).expanduser() if args.raw_dir else runtime_path("raw")
    if not raw_dir.exists():
        raise ConfigError(f"Raw evidence directory not found: {raw_dir}")
    schemas = load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
    backups = _backup_existing_db_files(target_db_path)
    ingest_result = ingest_raw_dir(raw_dir, target_db_path, schemas=schemas)
    changelog_run = record_changelog(target_db_path, full=True)
    with connect(target_db_path):
        pass
    payload = {
        "db": str(target_db_path),
        "raw_dir": str(raw_dir),
        "backups": [str(path) for path in backups],
        "ingest": _ingest_record(ingest_result),
        "changelog": _changelog_record(changelog_run),
    }
    if args.json:
        print(json.dumps(payload, default=str))
    else:
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
        print(f"Run:     {changelog_run.run_id}")
        print(f"Events:  {changelog_run.event_count}")
    return 0


__all__ = ["run_rebuild_db"]
