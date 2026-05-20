from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1


def ensure_feature_tables(conn: sqlite3.Connection) -> None:
    ensure_schema_metadata(conn)
    ensure_changelog_tables(conn)
    ensure_download_tables(conn)
    ensure_bundle_tables(conn)


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
            SUM(current_count) AS current_count,
            SUM(tombstone_count) AS tombstone_count,
            MAX(latest_modified_at) AS latest_modified_at,
            MAX(latest_ingested_at) AS latest_ingested_at
        FROM (
            SELECT
                endpoint,
                COUNT(*) AS current_count,
                0 AS tombstone_count,
                MAX(modified_at) AS latest_modified_at,
                MAX(ingested_at) AS latest_ingested_at
            FROM endpoint_records
            GROUP BY endpoint
            UNION ALL
            SELECT
                endpoint,
                0 AS current_count,
                COUNT(*) AS tombstone_count,
                MAX(modified_at) AS latest_modified_at,
                MAX(ingested_at) AS latest_ingested_at
            FROM endpoint_tombstones
            GROUP BY endpoint
        )
        GROUP BY endpoint;

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
