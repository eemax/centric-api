from __future__ import annotations

from pathlib import Path

from centric_api.db_schema import SCHEMA_VERSION
from centric_api.store import connect


def test_connect_installs_dashboard_views(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        schema_version = conn.execute(
            "SELECT value FROM local_metadata WHERE key = 'db_schema_version'"
        ).fetchone()[0]
        views = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'view' AND name LIKE 'dashboard_%'
                """
            ).fetchall()
        }

    assert schema_version == str(SCHEMA_VERSION)
    assert {
        "dashboard_latest_fetch_runs",
        "dashboard_endpoint_state",
        "dashboard_recent_changes",
        "dashboard_actor_activity",
        "dashboard_download_jobs",
        "dashboard_bundle_runs",
        "dashboard_bundle_file_changes",
    } <= views
