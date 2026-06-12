from __future__ import annotations

import sqlite3

from .modeling.tables import ensure_model_tables

SCHEMA_VERSION = 1
REBUILD_DB_MESSAGE = "stale local DB; run centric-api rebuild-db --yes"
REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
    "local_metadata": {
        "key",
        "value",
        "updated_at",
    },
    "applied_raw_files": {
        "file_path",
        "endpoint",
        "source_run_id",
        "is_delta",
        "record_count",
        "invalid_record_count",
        "content_sha256",
        "manifest_path",
        "manifest_sha256",
        "run_mode",
        "ingested_at",
    },
    "endpoint_records": {
        "endpoint",
        "record_id",
        "payload_json",
        "payload_sha256",
        "modified_at",
        "source_file",
        "source_run_id",
        "ingested_at",
    },
    "endpoint_tombstones": {
        "endpoint",
        "record_id",
        "payload_json",
        "payload_sha256",
        "modified_at",
        "source_file",
        "source_run_id",
        "ingested_at",
    },
    "endpoint_state": {
        "endpoint",
        "current_count",
        "tombstone_count",
        "latest_modified_at",
        "latest_ingested_at",
        "updated_at",
    },
    "ingest_warnings": {
        "endpoint",
        "record_id",
        "source_file",
        "warning",
        "created_at",
    },
    "endpoint_changelog_runs": {
        "run_id",
        "created_at",
        "changelog_source",
        "changelog_source_sha256",
        "endpoint_count",
        "record_count",
        "event_count",
        "full_refresh",
        "scoped_record_count",
    },
    "endpoint_changelog_index_current": {
        "endpoint",
        "record_id",
        "payload_hash",
        "payload_json",
        "changelog_source_sha256",
        "updated_at",
        "run_id",
    },
    "endpoint_change_events": {
        "run_id",
        "endpoint",
        "record_id",
        "changed_at",
        "change_type",
        "delete_type",
        "modified_at",
        "modified_by_id",
        "modified_by_name",
        "changed_fields_json",
        "previous_payload_json",
        "current_payload_json",
    },
    "endpoint_change_fields": {
        "run_id",
        "event_id",
        "endpoint",
        "record_id",
        "changed_at",
        "field",
        "field_change_type",
        "event_change_type",
        "delete_type",
        "modified_at",
        "modified_by_id",
        "modified_by_name",
        "previous_value_json",
        "current_value_json",
    },
    "endpoint_change_summary": {
        "run_id",
        "changed_at",
        "endpoint",
        "change_type",
        "delete_type",
        "count",
    },
    "endpoint_field_change_summary": {
        "run_id",
        "changed_at",
        "endpoint",
        "field",
        "field_change_type",
        "event_change_type",
        "count",
    },
    "endpoint_actor_change_summary": {
        "run_id",
        "changed_at",
        "endpoint",
        "modified_by_id",
        "modified_by_name",
        "change_type",
        "delete_type",
        "count",
    },
    "endpoint_actor_field_change_summary": {
        "run_id",
        "changed_at",
        "endpoint",
        "modified_by_id",
        "modified_by_name",
        "field",
        "field_change_type",
        "event_change_type",
        "count",
    },
    "download_runs": {
        "run_id",
        "job_name",
        "mode",
        "started_at",
        "finished_at",
        "manifest_path",
        "matched_count",
        "selected_count",
        "downloaded_count",
        "already_present_count",
        "failed_count",
        "skipped_count",
        "skipped_current_count",
        "dry_run_count",
        "superseded_count",
        "tombstoned_count",
        "dry_run",
    },
    "download_items": {
        "run_id",
        "job_name",
        "document_id",
        "document_name",
        "revision_id",
        "current_revision_id",
        "document_modified_at",
        "latest_at_run",
        "previous_downloaded_revision_id",
        "previous_was_outdated",
        "status",
        "file_path",
        "sha256",
        "bytes",
        "error",
        "source_refs_json",
        "created_at",
    },
    "download_current": {
        "job_name",
        "document_id",
        "revision_id",
        "document_name",
        "current_revision_id",
        "document_modified_at",
        "status",
        "file_path",
        "sha256",
        "bytes",
        "last_run_id",
        "selected_at",
        "tombstoned_at",
        "tombstone_reason",
        "source_refs_json",
    },
    "bundle_runs": {
        "run_id",
        "bundle_name",
        "download_job",
        "started_at",
        "finished_at",
        "manifest_path",
        "changelog_json_path",
        "changelog_md_path",
        "zip_path",
        "item_count",
        "added_count",
        "changed_count",
        "renamed_count",
        "removed_count",
        "unchanged_count",
        "missing_count",
        "dry_run",
    },
    "bundle_items": {
        "run_id",
        "bundle_name",
        "archive_path",
        "identity",
        "source_endpoint",
        "source_record_id",
        "source_label",
        "document_id",
        "revision_id",
        "file_path",
        "sha256",
        "bytes",
        "status",
        "change_type",
        "previous_archive_path",
        "previous_revision_id",
        "previous_sha256",
        "created_at",
    },
    "bundle_current": {
        "bundle_name",
        "archive_path",
        "identity",
        "source_endpoint",
        "source_record_id",
        "source_label",
        "document_id",
        "revision_id",
        "file_path",
        "sha256",
        "bytes",
        "last_run_id",
        "selected_at",
    },
    "model_runs": {
        "run_id",
        "model_name",
        "title",
        "output_table",
        "action",
        "status",
        "started_at",
        "finished_at",
        "row_count",
        "issue_count",
        "error_count",
        "warning_count",
        "metrics_json",
    },
    "model_run_issues": {
        "run_id",
        "severity",
        "code",
        "message",
        "endpoint",
        "record_id",
        "sample_json",
        "created_at",
    },
}
REQUIRED_DASHBOARD_VIEW_COLUMNS: dict[str, set[str]] = {
    "dashboard_latest_fetch_runs": {
        "run_id",
        "run_mode",
        "first_ingested_at",
        "last_ingested_at",
        "file_count",
        "record_count",
        "invalid_record_count",
    },
    "dashboard_endpoint_state": {
        "endpoint",
        "current_count",
        "tombstone_count",
        "latest_modified_at",
        "latest_ingested_at",
    },
    "dashboard_recent_changes": {
        "run_id",
        "endpoint",
        "record_id",
        "changed_at",
        "change_type",
        "delete_type",
        "modified_at",
        "modified_by_id",
        "modified_by_name",
        "changed_fields_json",
    },
    "dashboard_actor_activity": {
        "changed_date",
        "endpoint",
        "modified_by_id",
        "modified_by_name",
        "change_type",
        "delete_type",
        "change_count",
    },
    "dashboard_download_jobs": {
        "run_id",
        "job_name",
        "mode",
        "started_at",
        "finished_at",
        "matched_count",
        "selected_count",
        "downloaded_count",
        "already_present_count",
        "failed_count",
        "skipped_count",
        "skipped_current_count",
        "superseded_count",
        "tombstoned_count",
        "dry_run",
    },
    "dashboard_bundle_runs": {
        "run_id",
        "bundle_name",
        "download_job",
        "started_at",
        "finished_at",
        "zip_path",
        "item_count",
        "added_count",
        "changed_count",
        "renamed_count",
        "removed_count",
        "unchanged_count",
        "missing_count",
        "dry_run",
    },
    "dashboard_bundle_file_changes": {
        "run_id",
        "bundle_name",
        "archive_path",
        "identity",
        "source_endpoint",
        "source_record_id",
        "source_label",
        "document_id",
        "revision_id",
        "file_path",
        "sha256",
        "bytes",
        "status",
        "change_type",
        "previous_archive_path",
        "previous_revision_id",
        "previous_sha256",
        "created_at",
    },
}


def validate_schema_shape(conn: sqlite3.Connection) -> list[str]:
    failures: list[str] = []
    for table, required_columns in sorted(REQUIRED_TABLE_COLUMNS.items()):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            [table],
        ).fetchone()
        if row is None:
            failures.append(f"missing table {table}")
            continue
        columns = {
            str(column["name"] if isinstance(column, sqlite3.Row) else column[1])
            for column in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            failures.append(f"{table} missing columns: {', '.join(missing_columns)}")
    for view, required_columns in sorted(REQUIRED_DASHBOARD_VIEW_COLUMNS.items()):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
            [view],
        ).fetchone()
        if row is None:
            failures.append(f"missing view {view}")
            continue
        try:
            columns = {
                str(column["name"] if isinstance(column, sqlite3.Row) else column[1])
                for column in conn.execute(f"PRAGMA table_info({view})").fetchall()
            }
        except sqlite3.DatabaseError as exc:
            failures.append(f"{view} invalid: {exc}")
            continue
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            failures.append(f"{view} missing columns: {', '.join(missing_columns)}")
    return failures


def ensure_feature_tables(conn: sqlite3.Connection) -> None:
    ensure_schema_metadata(conn)
    ensure_endpoint_state_table(conn)
    ensure_changelog_tables(conn)
    ensure_download_tables(conn)
    ensure_bundle_tables(conn)
    ensure_model_tables(conn)


def ensure_schema_metadata(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS local_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO local_metadata (key, value, updated_at)
        VALUES ('db_schema_version', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        [str(SCHEMA_VERSION)],
    )


def ensure_endpoint_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_state (
            endpoint TEXT PRIMARY KEY,
            current_count INTEGER NOT NULL,
            tombstone_count INTEGER NOT NULL,
            latest_modified_at TEXT,
            latest_ingested_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )


def ensure_changelog_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS endpoint_changelog_runs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            changelog_source TEXT NOT NULL,
            changelog_source_sha256 TEXT NOT NULL,
            endpoint_count INTEGER NOT NULL,
            record_count INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            full_refresh INTEGER NOT NULL,
            scoped_record_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endpoint_changelog_index_current (
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            changelog_source_sha256 TEXT,
            updated_at TEXT NOT NULL,
            run_id TEXT NOT NULL,
            PRIMARY KEY (endpoint, record_id)
        );

        CREATE TABLE IF NOT EXISTS endpoint_change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            change_type TEXT NOT NULL,
            delete_type TEXT,
            modified_at TEXT,
            modified_by_id TEXT,
            modified_by_name TEXT,
            previous_hash TEXT,
            current_hash TEXT,
            changed_fields_json TEXT NOT NULL,
            previous_payload_json TEXT,
            current_payload_json TEXT
        );

        CREATE TABLE IF NOT EXISTS endpoint_change_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            record_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            field TEXT NOT NULL,
            field_change_type TEXT NOT NULL,
            event_change_type TEXT NOT NULL,
            delete_type TEXT,
            modified_at TEXT,
            modified_by_id TEXT,
            modified_by_name TEXT,
            previous_value_json TEXT,
            current_value_json TEXT,
            FOREIGN KEY (event_id) REFERENCES endpoint_change_events(id)
        );

        CREATE TABLE IF NOT EXISTS endpoint_change_summary (
            run_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            change_type TEXT NOT NULL,
            delete_type TEXT,
            count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endpoint_field_change_summary (
            run_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            field TEXT NOT NULL,
            field_change_type TEXT NOT NULL,
            event_change_type TEXT NOT NULL,
            count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endpoint_actor_change_summary (
            run_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            modified_by_id TEXT,
            modified_by_name TEXT,
            change_type TEXT NOT NULL,
            delete_type TEXT,
            count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endpoint_actor_field_change_summary (
            run_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            modified_by_id TEXT,
            modified_by_name TEXT,
            field TEXT NOT NULL,
            field_change_type TEXT NOT NULL,
            event_change_type TEXT NOT NULL,
            count INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_events_changed_at
        ON endpoint_change_events(changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_events_endpoint_changed_at
        ON endpoint_change_events(endpoint, changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_events_actor
        ON endpoint_change_events(modified_by_id, changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_fields_changed_at
        ON endpoint_change_fields(changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_fields_endpoint_field
        ON endpoint_change_fields(endpoint, field);

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_summary_changed_at
        ON endpoint_change_summary(changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_field_change_summary_endpoint_changed_at
        ON endpoint_field_change_summary(endpoint, changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_actor_change_summary_actor
        ON endpoint_actor_change_summary(modified_by_id, changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_actor_change_summary_endpoint_changed_at
        ON endpoint_actor_change_summary(endpoint, changed_at);

        CREATE INDEX IF NOT EXISTS idx_endpoint_actor_field_change_summary_endpoint_changed_at
        ON endpoint_actor_field_change_summary(endpoint, changed_at);
        """
    )


def ensure_changelog_read_indexes(
    conn: sqlite3.Connection,
    *,
    include_field_indexes: bool = False,
) -> None:
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_endpoint_change_events_endpoint_activity_at;
        DROP INDEX IF EXISTS idx_endpoint_change_events_actor_activity_at;
        DROP INDEX IF EXISTS idx_endpoint_change_fields_activity_at;
        DROP INDEX IF EXISTS idx_endpoint_change_fields_endpoint_activity_at;

        CREATE INDEX IF NOT EXISTS idx_endpoint_change_events_activity_at
        ON endpoint_change_events(COALESCE(modified_at, changed_at));
        """
    )
    if include_field_indexes:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_endpoint_change_fields_event_id
            ON endpoint_change_fields(event_id);
            """
        )


def ensure_download_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS download_runs (
            run_id TEXT PRIMARY KEY,
            job_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            matched_count INTEGER NOT NULL,
            selected_count INTEGER NOT NULL,
            downloaded_count INTEGER NOT NULL,
            already_present_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            skipped_current_count INTEGER NOT NULL,
            dry_run_count INTEGER NOT NULL,
            superseded_count INTEGER NOT NULL,
            tombstoned_count INTEGER NOT NULL,
            dry_run INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS download_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            job_name TEXT NOT NULL,
            document_id TEXT NOT NULL,
            document_name TEXT,
            revision_id TEXT NOT NULL,
            current_revision_id TEXT,
            document_modified_at TEXT,
            latest_at_run INTEGER NOT NULL,
            previous_downloaded_revision_id TEXT,
            previous_was_outdated INTEGER NOT NULL,
            status TEXT NOT NULL,
            file_path TEXT,
            sha256 TEXT,
            bytes INTEGER,
            error TEXT,
            source_refs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES download_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_download_items_document
        ON download_items(document_id, revision_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_download_items_status
        ON download_items(status, created_at);

        CREATE TABLE IF NOT EXISTS download_current (
            job_name TEXT NOT NULL,
            document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            document_name TEXT,
            current_revision_id TEXT,
            document_modified_at TEXT,
            status TEXT NOT NULL,
            file_path TEXT,
            sha256 TEXT,
            bytes INTEGER,
            last_run_id TEXT NOT NULL,
            selected_at TEXT,
            tombstoned_at TEXT,
            tombstone_reason TEXT,
            source_refs_json TEXT NOT NULL,
            PRIMARY KEY (job_name, document_id, revision_id)
        );

        CREATE INDEX IF NOT EXISTS idx_download_current_job_status
        ON download_current(job_name, status);

        CREATE INDEX IF NOT EXISTS idx_download_current_document
        ON download_current(document_id, revision_id);
        """
    )


def ensure_bundle_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bundle_runs (
            run_id TEXT PRIMARY KEY,
            bundle_name TEXT NOT NULL,
            download_job TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            changelog_json_path TEXT NOT NULL,
            changelog_md_path TEXT NOT NULL,
            zip_path TEXT,
            item_count INTEGER NOT NULL,
            added_count INTEGER NOT NULL,
            changed_count INTEGER NOT NULL,
            renamed_count INTEGER NOT NULL,
            removed_count INTEGER NOT NULL,
            unchanged_count INTEGER NOT NULL,
            missing_count INTEGER NOT NULL,
            dry_run INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bundle_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            bundle_name TEXT NOT NULL,
            archive_path TEXT NOT NULL,
            identity TEXT NOT NULL,
            source_endpoint TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_label TEXT NOT NULL,
            document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            file_path TEXT,
            sha256 TEXT,
            bytes INTEGER,
            status TEXT NOT NULL,
            change_type TEXT NOT NULL,
            previous_archive_path TEXT,
            previous_revision_id TEXT,
            previous_sha256 TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES bundle_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_bundle_items_bundle_archive
        ON bundle_items(bundle_name, archive_path, created_at);

        CREATE TABLE IF NOT EXISTS bundle_current (
            bundle_name TEXT NOT NULL,
            archive_path TEXT NOT NULL,
            identity TEXT NOT NULL,
            source_endpoint TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_label TEXT NOT NULL,
            document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            last_run_id TEXT NOT NULL,
            selected_at TEXT NOT NULL,
            PRIMARY KEY (bundle_name, identity)
        );

        CREATE INDEX IF NOT EXISTS idx_bundle_current_document
        ON bundle_current(document_id, revision_id);

        CREATE INDEX IF NOT EXISTS idx_bundle_current_archive
        ON bundle_current(bundle_name, archive_path);
        """
    )


def ensure_dashboard_views(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP VIEW IF EXISTS dashboard_latest_fetch_runs;
        CREATE VIEW dashboard_latest_fetch_runs AS
        SELECT
            source_run_id AS run_id,
            run_mode,
            MIN(ingested_at) AS first_ingested_at,
            MAX(ingested_at) AS last_ingested_at,
            COUNT(*) AS file_count,
            SUM(record_count) AS record_count,
            SUM(invalid_record_count) AS invalid_record_count
        FROM applied_raw_files
        GROUP BY source_run_id, run_mode;

        DROP VIEW IF EXISTS dashboard_endpoint_state;
        CREATE VIEW dashboard_endpoint_state AS
        SELECT
            endpoint,
            current_count,
            tombstone_count,
            latest_modified_at,
            latest_ingested_at
        FROM endpoint_state;

        DROP VIEW IF EXISTS dashboard_recent_changes;
        CREATE VIEW dashboard_recent_changes AS
        SELECT
            run_id,
            endpoint,
            record_id,
            changed_at,
            change_type,
            delete_type,
            modified_at,
            modified_by_id,
            modified_by_name,
            changed_fields_json
        FROM endpoint_change_events;

        DROP VIEW IF EXISTS dashboard_actor_activity;
        CREATE VIEW dashboard_actor_activity AS
        SELECT
            substr(changed_at, 1, 10) AS changed_date,
            endpoint,
            modified_by_id,
            modified_by_name,
            change_type,
            delete_type,
            COUNT(*) AS change_count
        FROM endpoint_change_events
        GROUP BY
            substr(changed_at, 1, 10),
            endpoint,
            modified_by_id,
            modified_by_name,
            change_type,
            delete_type;

        DROP VIEW IF EXISTS dashboard_download_jobs;
        CREATE VIEW dashboard_download_jobs AS
        SELECT
            run_id,
            job_name,
            mode,
            started_at,
            finished_at,
            matched_count,
            selected_count,
            downloaded_count,
            already_present_count,
            failed_count,
            skipped_count,
            skipped_current_count,
            superseded_count,
            tombstoned_count,
            dry_run
        FROM download_runs;

        DROP VIEW IF EXISTS dashboard_bundle_runs;
        CREATE VIEW dashboard_bundle_runs AS
        SELECT
            run_id,
            bundle_name,
            download_job,
            started_at,
            finished_at,
            zip_path,
            item_count,
            added_count,
            changed_count,
            renamed_count,
            removed_count,
            unchanged_count,
            missing_count,
            dry_run
        FROM bundle_runs;

        DROP VIEW IF EXISTS dashboard_bundle_file_changes;
        CREATE VIEW dashboard_bundle_file_changes AS
        SELECT
            run_id,
            bundle_name,
            archive_path,
            identity,
            source_endpoint,
            source_record_id,
            source_label,
            document_id,
            revision_id,
            file_path,
            sha256,
            bytes,
            status,
            change_type,
            previous_archive_path,
            previous_revision_id,
            previous_sha256,
            created_at
        FROM bundle_items
        WHERE change_type <> 'unchanged';
        """
    )
