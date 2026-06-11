from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..auth import resolve_credentials
from ..bundle_config import load_bundle_config
from ..config import load_fetcher_settings, runtime_home, runtime_path
from ..db_schema import REBUILD_DB_MESSAGE, SCHEMA_VERSION, validate_schema_shape
from ..defaults import (
    DEFAULT_BUNDLE_LOCK_PATH,
    DEFAULT_CRON_LOG_PATH,
    DEFAULT_DOWNLOAD_LOCK_PATH,
    DEFAULT_DOWNLOAD_LOG_PATH,
    DEFAULT_FETCH_LOG_PATH,
    DEFAULT_LOCK_PATH,
    db_path,
)
from ..download_config import load_download_config
from ..schema import load_endpoint_schemas
from ..store import IngestResult, connect_readonly, endpoint_has_cache_evidence, table_exists
from ..time_display import format_time_ago


def _status_payload(target_db_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runtime_home": str(runtime_home()),
        "db": str(target_db_path),
        "db_exists": target_db_path.is_file(),
        "logs": {
            "fetch": str(runtime_path(DEFAULT_FETCH_LOG_PATH)),
            "download": str(runtime_path(DEFAULT_DOWNLOAD_LOG_PATH)),
            "cron": str(runtime_path(DEFAULT_CRON_LOG_PATH)),
        },
        "locks": {
            "fetch": _lock_record(runtime_path(DEFAULT_LOCK_PATH)),
            "download": _lock_record(runtime_path(DEFAULT_DOWNLOAD_LOCK_PATH)),
            "bundle": _lock_record(runtime_path(DEFAULT_BUNDLE_LOCK_PATH)),
        },
        "latest_fetch": None,
        "endpoint_state": [],
        "latest_changelog": None,
        "latest_download": None,
        "latest_bundle": None,
    }
    if not target_db_path.is_file():
        return payload
    with connect_readonly(target_db_path) as conn:
        payload["latest_fetch"] = _first_row(
            conn,
            "applied_raw_files",
            """
            SELECT source_run_id AS run_id, run_mode, MAX(ingested_at) AS ingested_at,
                   COUNT(*) AS file_count, SUM(record_count) AS record_count
            FROM applied_raw_files
            GROUP BY source_run_id, run_mode
            ORDER BY ingested_at DESC
            LIMIT 1
            """,
        )
        payload["endpoint_state"] = _all_rows(
            conn,
            "endpoint_records",
            """
            SELECT endpoint, COUNT(*) AS current_count, MAX(modified_at) AS latest_modified_at
            FROM endpoint_records
            GROUP BY endpoint
            ORDER BY endpoint
            """,
        )
        payload["latest_changelog"] = _first_row(
            conn,
            "endpoint_changelog_runs",
            """
            SELECT run_id, created_at, endpoint_count, record_count, event_count,
                   full_refresh, scoped_record_count
            FROM endpoint_changelog_runs
            ORDER BY created_at DESC, run_id DESC
            LIMIT 1
            """,
        )
        payload["latest_download"] = _first_row(
            conn,
            "download_runs",
            """
            SELECT run_id, job_name, mode, finished_at, matched_count, selected_count,
                   downloaded_count, failed_count
            FROM download_runs
            ORDER BY finished_at DESC, run_id DESC
            LIMIT 1
            """,
        )
        payload["latest_bundle"] = _first_row(
            conn,
            "bundle_runs",
            """
            SELECT run_id, bundle_name, download_job, finished_at, zip_path, item_count,
                   added_count, changed_count, removed_count
            FROM bundle_runs
            ORDER BY finished_at DESC, run_id DESC
            LIMIT 1
            """,
        )
    return payload


def _doctor_checks(args: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    target_db_path = db_path(args.db)
    fetcher_loaded = None
    download_config = None
    bundle_config = None
    try:
        fetcher_loaded = load_fetcher_settings(args.fetch_config)
    except Exception as exc:
        checks.append(_check("FAIL", "fetch_config", str(exc)))
    else:
        checks.append(_check("OK", "fetch_config", f"loaded {args.fetch_config}"))

    try:
        load_endpoint_schemas(Path(args.schema).expanduser() if args.schema else None)
    except Exception as exc:
        checks.append(_check("FAIL", "schema", str(exc)))
    else:
        checks.append(_check("OK", "schema", "loaded endpoint schema"))

    try:
        download_config = load_download_config(args.download_config)
    except Exception as exc:
        checks.append(_check("FAIL", "download_config", str(exc)))
    else:
        checks.append(_check("OK", "download_config", f"loaded {download_config.path}"))

    try:
        bundle_config = load_bundle_config(args.bundle_config)
    except Exception as exc:
        checks.append(_check("FAIL", "bundle_config", str(exc)))
    else:
        checks.append(_check("OK", "bundle_config", f"loaded {bundle_config.path}"))

    if fetcher_loaded is not None:
        _fetcher_cfg, auth_settings, _endpoint_specs = fetcher_loaded
        try:
            base_url, username, password = resolve_credentials(
                auth_settings,
                env_file=(
                    Path(args.env_file).expanduser() if args.env_file else auth_settings.env_file
                ),
            )
        except Exception as exc:
            checks.append(_check("FAIL", "credentials", str(exc)))
        else:
            if username and password:
                checks.append(_check("OK", "credentials", f"found credentials for {base_url}"))
            else:
                checks.append(
                    _check(
                        "WARN",
                        "credentials",
                        f"CENTRIC_BASE_URL resolves to {base_url}, but username/password "
                        "are incomplete.",
                    )
                )

    if target_db_path.is_file():
        checks.append(_check("OK", "db", f"SQLite database exists: {target_db_path}"))
        with connect_readonly(target_db_path) as conn:
            _doctor_db_checks(conn, checks)
            _doctor_download_checks(conn, checks, download_config)
            _doctor_bundle_checks(conn, checks, bundle_config)
    else:
        checks.append(_check("FAIL", "db", f"SQLite database not found: {target_db_path}"))

    for name, path in (
        ("fetch_lock", runtime_path(DEFAULT_LOCK_PATH)),
        ("download_lock", runtime_path(DEFAULT_DOWNLOAD_LOCK_PATH)),
        ("bundle_lock", runtime_path(DEFAULT_BUNDLE_LOCK_PATH)),
    ):
        if path.exists():
            checks.append(_check("WARN", name, f"Lock file exists: {path}"))
        else:
            checks.append(_check("OK", name, "no lock file"))
    return checks


def _doctor_db_checks(conn: sqlite3.Connection, checks: list[dict[str, Any]]) -> None:
    if table_exists(conn, "local_metadata"):
        row = conn.execute(
            "SELECT value FROM local_metadata WHERE key = 'db_schema_version'"
        ).fetchone()
        actual_version = int(row["value"]) if row is not None and str(row["value"]).isdigit() else 0
        if actual_version == SCHEMA_VERSION:
            checks.append(_check("OK", "db_schema_version", str(actual_version)))
        else:
            checks.append(
                _check(
                    "FAIL",
                    "db_schema_version",
                    f"expected {SCHEMA_VERSION}, found {actual_version or 'missing'}",
                )
            )
    else:
        checks.append(_check("FAIL", "db_schema_version", "local_metadata table missing"))
    for table in ("endpoint_records", "applied_raw_files"):
        if table_exists(conn, table):
            checks.append(_check("OK", table, "table exists"))
        else:
            checks.append(_check("FAIL", table, "table missing"))
    schema_failures = validate_schema_shape(conn)
    if schema_failures:
        preview = "; ".join(schema_failures[:3])
        if len(schema_failures) > 3:
            preview += f"; {len(schema_failures) - 3} more"
        checks.append(
            _check(
                "FAIL",
                "db_schema_shape",
                f"{REBUILD_DB_MESSAGE}: {preview}",
                repair="centric-api rebuild-db --yes",
            )
        )
    else:
        checks.append(_check("OK", "db_schema_shape", "current"))
    if table_exists(conn, "endpoint_records"):
        count = conn.execute("SELECT COUNT(*) FROM endpoint_records").fetchone()[0]
        checks.append(_check("OK" if count else "WARN", "endpoint_records_count", f"{count} rows"))
    if table_exists(conn, "endpoint_changelog_runs"):
        count = conn.execute("SELECT COUNT(*) FROM endpoint_changelog_runs").fetchone()[0]
        checks.append(_check("OK" if count else "WARN", "changelog_runs", f"{count} runs"))
    else:
        checks.append(_check("WARN", "changelog_runs", "changelog tables not created yet"))


def _doctor_download_checks(
    conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    config: Any | None,
) -> None:
    if config is None:
        return
    for job in config.jobs:
        missing = [
            endpoint
            for endpoint in sorted(_download_required_endpoints(job))
            if not endpoint_has_cache_evidence(conn, endpoint)
        ]
        if missing:
            checks.append(
                _check(
                    "FAIL",
                    f"download_job:{job.name}",
                    f"missing cached endpoints: {', '.join(missing)}",
                )
            )
        else:
            checks.append(_check("OK", f"download_job:{job.name}", "required endpoints cached"))
    if table_exists(conn, "download_current"):
        rows = conn.execute(
            """
            SELECT job_name, document_id, revision_id, file_path
            FROM download_current
            WHERE status = 'current' AND file_path IS NOT NULL
            """
        ).fetchall()
        missing_files = [row for row in rows if not Path(str(row["file_path"])).is_file()]
        if missing_files:
            first = missing_files[0]
            checks.append(
                _check(
                    "FAIL",
                    "download_current_files",
                    f"{len(missing_files)} missing files; first {first['document_id']} at "
                    f"{first['file_path']}",
                )
            )
        else:
            checks.append(_check("OK", "download_current_files", f"{len(rows)} files present"))


def _doctor_bundle_checks(
    conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    config: Any | None,
) -> None:
    if config is None:
        return
    for job in config.bundles:
        if not table_exists(conn, "download_current"):
            checks.append(
                _check("FAIL", f"bundle_job:{job.name}", "download_current table missing")
            )
            continue
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM download_current
            WHERE job_name = ? AND status = 'current'
            """,
            [job.download_job],
        ).fetchone()
        count = int(row["count"] or 0)
        if count:
            checks.append(
                _check("OK", f"bundle_job:{job.name}", f"{count} current downloaded files")
            )
        else:
            checks.append(
                _check(
                    "WARN",
                    f"bundle_job:{job.name}",
                    f"no current downloads for job {job.download_job}",
                )
            )


def _download_required_endpoints(job: Any) -> set[str]:
    endpoints = {source.endpoint for source in job.sources}
    endpoints.add("documents")
    endpoints.add("document_revisions")
    for source in job.sources:
        endpoints.update(_lookup_endpoints_from_filters_for_doctor(source.filters))
    endpoints.update(_lookup_endpoints_from_filters_for_doctor(job.document_filters))
    endpoints.update(_lookup_endpoints_from_filters_for_doctor(job.revision_filters))
    return endpoints


def _lookup_endpoints_from_filters_for_doctor(filters: Any) -> set[str]:
    return {item.lookup.endpoint for item in filters if getattr(item, "lookup", None) is not None}


def _check(
    status: str,
    name: str,
    message: str,
    *,
    repair: str | None = None,
) -> dict[str, Any]:
    payload = {"status": status, "name": name, "message": message}
    if repair is not None:
        payload["repair"] = repair
    return payload


def _first_row(conn: sqlite3.Connection, table: str, query: str) -> dict[str, Any] | None:
    if not table_exists(conn, table):
        return None
    row = conn.execute(query).fetchone()
    return dict(row) if row is not None else None


def _all_rows(conn: sqlite3.Connection, table: str, query: str) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    return [dict(row) for row in conn.execute(query).fetchall()]


def _lock_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def _print_human_status(payload: dict[str, Any]) -> None:
    print("Centric API Status")
    print()
    print(f"Home: {payload['runtime_home']}")
    print(f"DB:   {payload['db']}{'' if payload['db_exists'] else ' (missing)'}")
    print()
    print("Health")
    print(f"  Fetch lock:     {_lock_status(payload['locks']['fetch'])}")
    print(f"  Download lock:  {_lock_status(payload['locks']['download'])}")
    print(f"  Bundle lock:    {_lock_status(payload['locks']['bundle'])}")
    print()
    print("Latest Runs")
    print(f"  Fetch:      {_latest_fetch_status(payload['latest_fetch'])}")
    print(f"  Changelog:  {_latest_changelog_status(payload['latest_changelog'])}")
    print(f"  Download:   {_latest_download_status(payload['latest_download'])}")
    print(f"  Bundle:     {_latest_bundle_status(payload['latest_bundle'])}")
    endpoint_rows = payload["endpoint_state"]
    if endpoint_rows:
        total_records = sum(int(row["current_count"] or 0) for row in endpoint_rows)
        latest_modified = max(
            (str(row["latest_modified_at"]) for row in endpoint_rows if row["latest_modified_at"]),
            default="none",
        )
        print()
        print("Data")
        print(f"  Endpoints:        {_format_status_count(len(endpoint_rows))}")
        print(f"  Records:          {_format_status_count(total_records)} current")
        print(f"  Latest modified:  {format_time_ago(latest_modified)}")
        print()
        print("Endpoints")
        endpoint_width = max(len("Endpoint"), *(len(row["endpoint"]) for row in endpoint_rows))
        for row in endpoint_rows:
            latest = format_time_ago(row["latest_modified_at"])
            print(
                f"  {row['endpoint']:<{endpoint_width}}  "
                f"{_format_status_count(int(row['current_count'] or 0)):>10}  "
                f"latest {latest}"
            )


def _lock_status(lock: dict[str, Any]) -> str:
    return "present" if lock["exists"] else "clear"


def _latest_fetch_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "none"
    return (
        f"{format_time_ago(row['ingested_at'])}  {row['run_mode'] or 'unknown'}  "
        f"{_format_status_count(int(row['file_count'] or 0))} endpoints  "
        f"{_format_status_count(int(row['record_count'] or 0))} records"
    )


def _latest_changelog_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "none"
    return (
        f"{format_time_ago(row['created_at'])}  "
        f"{_format_status_count(int(row['event_count'] or 0))} events  "
        f"{_format_status_count(int(row['endpoint_count'] or 0))} endpoints"
    )


def _latest_download_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "none"
    return (
        f"{format_time_ago(row['finished_at'])}  {row['job_name']}  "
        f"{_format_status_count(int(row['downloaded_count'] or 0))} downloaded, "
        f"{_format_status_count(int(row['failed_count'] or 0))} failed"
    )


def _latest_bundle_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "none"
    return (
        f"{format_time_ago(row['finished_at'])}  {row['bundle_name']}  "
        f"{_format_status_count(int(row['item_count'] or 0))} files"
    )


def _format_status_count(value: int) -> str:
    return f"{value:,}"


def _print_human_doctor(checks: list[dict[str, Any]]) -> None:
    print("Centric API Doctor")
    print()
    counts = _doctor_status_counts(checks)
    print(
        f"Result: {_doctor_result(counts)}  "
        f"{counts['OK']} ok, {counts['WARN']} warn, {counts['FAIL']} fail"
    )
    grouped = _group_doctor_checks(checks)
    for group in ("Setup", "Database", "Downloads", "Bundles", "Runtime", "Other"):
        group_checks = grouped.get(group, [])
        if not group_checks:
            continue
        print()
        print(group)
        label_width = max(len(_doctor_check_label(check)) for check in group_checks)
        for check in group_checks:
            print(
                f"  {check['status']:<4}  "
                f"{_doctor_check_label(check):<{label_width}}  "
                f"{check['message']}"
            )
            if check.get("repair"):
                print(f"        repair: {check['repair']}")


def _doctor_status_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "OK": sum(1 for check in checks if check["status"] == "OK"),
        "WARN": sum(1 for check in checks if check["status"] == "WARN"),
        "FAIL": sum(1 for check in checks if check["status"] == "FAIL"),
    }


def _doctor_result(counts: dict[str, int]) -> str:
    if counts["FAIL"]:
        return "FAIL"
    if counts["WARN"]:
        return "WARN"
    return "OK"


def _group_doctor_checks(checks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        grouped.setdefault(_doctor_check_group(str(check["name"])), []).append(check)
    return grouped


def _doctor_check_group(name: str) -> str:
    if name in {
        "fetch_config",
        "schema",
        "download_config",
        "bundle_config",
        "credentials",
    }:
        return "Setup"
    if name in {
        "db",
        "db_schema_version",
        "endpoint_records",
        "applied_raw_files",
        "db_schema_shape",
        "endpoint_records_count",
        "changelog_runs",
    }:
        return "Database"
    if name.startswith("download_job:") or name == "download_current_files":
        return "Downloads"
    if name.startswith("bundle_job:"):
        return "Bundles"
    if name in {"fetch_lock", "download_lock", "bundle_lock"}:
        return "Runtime"
    return "Other"


def _doctor_check_label(check: dict[str, Any]) -> str:
    name = str(check["name"])
    labels = {
        "fetch_config": "fetch config",
        "download_config": "download config",
        "bundle_config": "bundle config",
        "db_schema_version": "schema version",
        "db_schema_shape": "schema shape",
        "endpoint_records": "endpoint records",
        "applied_raw_files": "raw files",
        "endpoint_records_count": "endpoint records",
        "changelog_runs": "changelog runs",
        "download_current_files": "current files",
        "fetch_lock": "fetch lock",
        "download_lock": "download lock",
        "bundle_lock": "bundle lock",
    }
    if name.startswith("download_job:"):
        return f"download job {name.split(':', 1)[1]}"
    if name.startswith("bundle_job:"):
        return f"bundle job {name.split(':', 1)[1]}"
    return labels.get(name, name.replace("_", " "))


def _ingest_record(result: IngestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "applied_files": result.applied_files,
        "skipped_files": result.skipped_files,
        "records_read": result.records_read,
        "records_upserted": result.records_upserted,
        "records_deleted": result.records_deleted,
        "records_hard_deleted": result.records_hard_deleted,
        "invalid_records": result.invalid_records,
    }


def _changelog_record(run: Any) -> dict[str, Any]:
    return {
        "status": "updated",
        "run_id": run.run_id,
        "endpoint_count": run.endpoint_count,
        "record_count": run.record_count,
        "event_count": run.event_count,
        "full_refresh": run.full_refresh,
        "scoped_record_count": run.scoped_record_count,
    }
