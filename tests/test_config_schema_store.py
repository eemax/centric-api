from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from centric_api.bundle_config import load_bundle_config
from centric_api.config import (
    ConfigError,
    load_fetcher_settings,
    resolve_optional_private_config_path,
    resolve_private_config_path,
    runtime_home,
    runtime_path,
)
from centric_api.db_schema import SCHEMA_VERSION
from centric_api.download_config import load_download_config
from centric_api.fetch_common import FetchError
from centric_api.fetch_pagination import get_expected_count
from centric_api.fetcher import run_endpoint
from centric_api.load_config import load_load_config
from centric_api.models import CountSpec, EndpointSpec, FetcherConfig
from centric_api.schema import load_endpoint_schemas
from centric_api.store import connect, ingest_raw_dir
from centric_api.units import load_unit_registry
from centric_api.view_config import load_view_config


class _JsonResponse:
    status_code = 200
    reason_phrase = "OK"
    headers = {"content-type": "application/json"}

    def __init__(self, payload: dict):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_runtime_paths_use_centric_api_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))

    assert runtime_home() == tmp_path / "home"
    assert runtime_path("raw") == tmp_path / "home" / "raw"


def test_explicit_private_config_paths_expand_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "user-home"
    monkeypatch.setenv("HOME", str(home))

    assert resolve_private_config_path("delta/state.json", "~/custom-state.json") == (
        home / "custom-state.json"
    )
    assert resolve_optional_private_config_path("load.yml", "~/custom-load.yml") == (
        home / "custom-load.yml"
    )


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


def test_default_configs_load_outside_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))

    fetcher_cfg, auth_settings, endpoints = load_fetcher_settings(Path("config/fetcher.yml"))
    download_config = load_download_config()
    bundle_config = load_bundle_config()
    view_config = load_view_config()
    load_config = load_load_config()
    units = load_unit_registry()
    endpoint_schemas = load_endpoint_schemas()

    assert fetcher_cfg.output_dir == tmp_path / "home" / "raw"
    assert auth_settings.env_file == tmp_path / "home" / "local.env"
    assert endpoints
    assert "product_sources" in {endpoint.name for endpoint in endpoints}
    bom_subtypes = next(endpoint for endpoint in endpoints if endpoint.name == "bom_subtypes")
    assert bom_subtypes.path == "apparel_bom_subtypes"
    assert bom_subtypes.count_spec.path == "count/ApparelBOMSubtype"
    assert download_config.jobs
    assert bundle_config.bundles
    assert view_config.views
    assert load_config.jobs
    assert units.dimensions
    assert "styles" in endpoint_schemas
    assert "product_sources" in endpoint_schemas
    assert "bom_subtypes" in endpoint_schemas
    bom_sections_schema = endpoint_schemas["bom_sections"]
    assert ("active", False) in {
        (condition.field, condition.equals) for condition in bom_sections_schema.delete_when_any
    }
    assert ("ad_hoc", True) in {
        (condition.field, condition.equals) for condition in bom_sections_schema.delete_when_any
    }


def test_load_fetcher_settings_runtime_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )

    fetcher_cfg, auth_settings, endpoints = load_fetcher_settings(config)

    assert fetcher_cfg.output_dir == tmp_path / "home" / "raw"
    assert fetcher_cfg.checkpoint_dir == tmp_path / "home" / "checkpoints"
    assert auth_settings.env_file == tmp_path / "home" / "local.env"
    assert [endpoint.name for endpoint in endpoints] == ["styles"]
    assert endpoints[0].count_spec.path == "count/Style"


def test_load_fetcher_settings_expands_user_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = home / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )

    fetcher_cfg, _auth_settings, endpoints = load_fetcher_settings("~/fetcher.yml")

    assert fetcher_cfg.timeout == 5
    assert [endpoint.name for endpoint in endpoints] == ["styles"]


def test_load_fetcher_settings_expands_user_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
env_file: ~/centric.env
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )

    _fetcher_cfg, auth_settings, _endpoints = load_fetcher_settings(config)

    assert auth_settings.env_file == home / "centric.env"


def test_load_fetcher_settings_requires_count_spec(tmp_path: Path) -> None:
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="count_spec"):
        load_fetcher_settings(config)


def test_load_fetcher_settings_rejects_unknown_keys(tmp_path: Path) -> None:
    root_config = tmp_path / "fetcher-root.yml"
    root_config.write_text(
        """
timeout: 5
typo: nope
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )
    endpoint_config = tmp_path / "fetcher-endpoint.yml"
    endpoint_config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    typo_endpoint: nope
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )
    count_config = tmp_path / "fetcher-count.yml"
    count_config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
      typo_count: nope
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="fetcher config has unknown keys: typo"):
        load_fetcher_settings(root_config)
    with pytest.raises(ConfigError, match="endpoint has unknown keys: typo_endpoint"):
        load_fetcher_settings(endpoint_config)
    with pytest.raises(ConfigError, match="count_spec has unknown keys: typo_count"):
        load_fetcher_settings(count_config)


def test_count_preflight_rejects_fractional_counts(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

    class Response:
        status_code = 200
        reason_phrase = "OK"
        text = '{"count": 1.5}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"count": 1.5}

    def request(*_args, **_kwargs):
        return Response()

    auth = Auth()
    auth.request = request
    config = tmp_path / "fetcher.yml"
    config.write_text(
        """
timeout: 5
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: count/Style
""",
        encoding="utf-8",
    )
    fetcher_cfg, _auth_settings, endpoints = load_fetcher_settings(config)

    with pytest.raises(FetchError, match="non-integer"):
        get_expected_count(endpoints[0], auth, fetcher_cfg)


def test_summary_request_failure_logs_exact_request_url(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

        def request(self, *_args, **_kwargs):
            class Response:
                status_code = 400
                reason_phrase = "Bad Request"
                text = '{"error": "bad filter"}'
                headers = {"content-type": "application/json"}

            return Response()

    spec = EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        count_spec=CountSpec(path="count/Style", query_params={"foo": "bar"}),
    )
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    events = []

    with pytest.raises(FetchError, match="non-retryable HTTP 400"):
        get_expected_count(spec, Auth(), fetcher_cfg, api_log_callback=events.append)

    request_failed = next(event for event in events if event.get("event") == "request_failed")
    assert request_failed["level"] == "summary"
    assert request_failed["endpoint"] == "styles"
    assert request_failed["request_kind"] == "count preflight"
    assert request_failed["method"] == "GET"
    assert request_failed["url"] == (
        "https://centric.example.com/api/v2/count/Style?foo=bar&decoded=true"
    )


def test_delta_zero_count_skips_empty_raw_file(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

        def request(self, *_args, **_kwargs):
            return _JsonResponse({"count": 0})

    spec = EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        count_spec=CountSpec(path="count/Style"),
    )
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )

    result = run_endpoint(
        spec,
        Auth(),
        fetcher_cfg,
        append_output=True,
        output_file_suffix=".delta",
        create_empty_output=False,
    )

    assert result.items_fetched == 0
    assert result.expected_count == 0
    assert not result.output_file_created
    assert not result.output_file.exists()
    checkpoint = json.loads(result.checkpoint_file.read_text(encoding="utf-8"))
    assert checkpoint["completed"] is True
    assert "output_file" not in checkpoint


def test_full_zero_count_keeps_empty_raw_file(tmp_path: Path) -> None:
    class Auth:
        base_url = "https://centric.example.com"

        def request(self, *_args, **_kwargs):
            return _JsonResponse({"count": 0})

    spec = EndpointSpec(
        name="styles",
        api_version="v2",
        path="styles",
        count_spec=CountSpec(path="count/Style"),
    )
    fetcher_cfg = FetcherConfig(
        base_url="https://centric.example.com",
        output_dir=tmp_path / "raw",
        checkpoint_dir=tmp_path / "checkpoints",
    )

    result = run_endpoint(spec, Auth(), fetcher_cfg, create_empty_output=True)

    assert result.items_fetched == 0
    assert result.expected_count == 0
    assert result.output_file_created
    assert result.output_file.is_file()
    assert result.output_file.read_text(encoding="utf-8") == ""


def test_schema_requires_endpoints_root(tmp_path: Path) -> None:
    schema = tmp_path / "endpoint-schema.yml"
    schema.write_text(
        """
styles:
  delete_when_any:
    - field: active
      equals: false
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="endpoints"):
        load_endpoint_schemas(schema)


def test_endpoint_schema_rejects_unknown_keys_and_versions(tmp_path: Path) -> None:
    root_schema = tmp_path / "root-schema.yml"
    root_schema.write_text(
        """
version: 1
unknown: nope
endpoints: {}
""",
        encoding="utf-8",
    )
    version_schema = tmp_path / "version-schema.yml"
    version_schema.write_text(
        """
version: 2
endpoints: {}
""",
        encoding="utf-8",
    )
    endpoint_schema = tmp_path / "endpoint-schema.yml"
    endpoint_schema.write_text(
        """
version: 1
endpoints:
  styles:
    typo: nope
""",
        encoding="utf-8",
    )
    condition_schema = tmp_path / "condition-schema.yml"
    condition_schema.write_text(
        """
version: 1
endpoints:
  styles:
    delete_when_any:
      - field: active
        equals: false
        typo: nope
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown keys: unknown"):
        load_endpoint_schemas(root_schema)
    with pytest.raises(ConfigError, match="version must be 1"):
        load_endpoint_schemas(version_schema)
    with pytest.raises(ConfigError, match="unknown keys: typo"):
        load_endpoint_schemas(endpoint_schema)
    with pytest.raises(ConfigError, match="unknown keys: typo"):
        load_endpoint_schemas(condition_schema)


def test_ingest_applies_endpoint_tombstone_rules(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z", "active": True}),
                json.dumps({"id": "S1", "_modified_at": "2026-01-02T00:00:00Z", "active": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yml"
    schema_path.write_text(
        """
endpoints:
  styles:
    delete_when_any:
      - field: active
        equals: false
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    result = ingest_raw_dir(raw_dir, db_path, schemas=load_endpoint_schemas(schema_path))

    assert result.records_read == 2
    assert result.records_upserted == 0
    assert result.records_deleted == 0
    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT COUNT(*) FROM endpoint_records").fetchone()[0]
        tombstones = conn.execute("SELECT COUNT(*) FROM endpoint_tombstones").fetchone()[0]
    assert current == 0
    assert tombstones == 1


def test_default_schema_tombstones_ad_hoc_bom_sections(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "bom_sections.jsonl").write_text(
        json.dumps(
            {
                "id": "BS1",
                "_modified_at": "2026-01-01T00:00:00Z",
                "node_name": "Custom Section",
                "active": True,
                "ad_hoc": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"bom_sections": {"file": "bom_sections.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    result = ingest_raw_dir(raw_dir, db_path, schemas=load_endpoint_schemas())

    assert result.records_read == 1
    assert result.records_upserted == 0
    assert result.records_deleted == 0
    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT COUNT(*) FROM endpoint_records").fetchone()[0]
        tombstone = conn.execute(
            """
            SELECT payload_json
            FROM endpoint_tombstones
            WHERE endpoint = 'bom_sections' AND record_id = 'BS1'
            """
        ).fetchone()
    assert current == 0
    assert tombstone is not None
    assert json.loads(tombstone[0])["ad_hoc"] is True


def test_default_schema_tombstones_compositions_not_ok_for_material(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "compositions.jsonl").write_text(
        json.dumps(
            {
                "id": "COMP1",
                "_modified_at": "2026-01-01T00:00:00Z",
                "node_name": "Retired Composition",
                "ok_for_material": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"compositions": {"file": "compositions.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    result = ingest_raw_dir(raw_dir, db_path, schemas=load_endpoint_schemas())

    assert result.records_read == 1
    assert result.records_upserted == 0
    assert result.records_deleted == 0
    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT COUNT(*) FROM endpoint_records").fetchone()[0]
        tombstone = conn.execute(
            """
            SELECT payload_json
            FROM endpoint_tombstones
            WHERE endpoint = 'compositions' AND record_id = 'COMP1'
            """
        ).fetchone()
    assert current == 0
    assert tombstone is not None
    assert json.loads(tombstone[0])["ok_for_material"] is False


def test_ingest_rejects_manifest_drift_for_applied_raw_file(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "delta",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": True}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"
    first = ingest_raw_dir(raw_dir, db_path, schemas={})
    assert first.applied_files == 1

    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Raw manifest changed after ingest"):
        ingest_raw_dir(raw_dir, db_path, schemas={})


def test_manifest_scoped_ingest_ignores_unlisted_jsonl_files(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "materials.jsonl").write_text(
        json.dumps({"id": "M1", "_modified_at": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    result = ingest_raw_dir(raw_dir, db_path, schemas={})

    assert result.records_read == 1
    assert result.endpoints == {"styles": 1}
    with sqlite3.connect(db_path) as conn:
        endpoints = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT endpoint FROM endpoint_records ORDER BY endpoint"
            ).fetchall()
        ]
    assert endpoints == ["styles"]


def test_ingest_refreshes_endpoint_state(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"}),
                json.dumps({"id": "S2", "_modified_at": "2026-01-02T00:00:00Z"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "full",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"

    result = ingest_raw_dir(raw_dir, db_path, schemas={})

    assert result.records_upserted == 2
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            """
            SELECT endpoint, current_count, tombstone_count, latest_modified_at
            FROM endpoint_state
            """
        ).fetchone()
        dashboard_state = conn.execute(
            """
            SELECT endpoint, current_count, tombstone_count, latest_modified_at
            FROM dashboard_endpoint_state
            """
        ).fetchone()
    assert state == ("styles", 2, 0, "2026-01-02T00:00:00Z")
    assert dashboard_state == state


def test_first_endpoint_state_refresh_backfills_existing_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO endpoint_records (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "materials",
                "M1",
                json.dumps({"id": "M1", "_modified_at": "2026-01-01T00:00:00Z"}),
                "material-1",
                "2026-01-01T00:00:00Z",
                "materials.jsonl",
                "run-0",
                "2026-01-01T00:00:00Z",
            ],
        )
    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S1", "_modified_at": "2026-01-02T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "mode": "delta",
                "started_at": "2026-01-02T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": True}},
            }
        ),
        encoding="utf-8",
    )

    result = ingest_raw_dir(raw_dir, db_path, schemas={})

    assert result.records_upserted == 1
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT endpoint, current_count, latest_modified_at
            FROM endpoint_state
            ORDER BY endpoint
            """
        ).fetchall()
    assert rows == [
        ("materials", 1, "2026-01-01T00:00:00Z"),
        ("styles", 1, "2026-01-02T00:00:00Z"),
    ]


def test_full_ingest_hard_deletes_missing_current_records(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO endpoint_records (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?),
                (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "styles",
                "S1",
                json.dumps({"id": "S1", "_modified_at": "2026-01-01T00:00:00Z"}),
                "hash-s1",
                "2026-01-01T00:00:00Z",
                "seed.jsonl",
                "seed",
                "2026-01-01T00:00:00Z",
                "styles",
                "S2",
                json.dumps({"id": "S2", "_modified_at": "2026-01-01T00:00:00Z"}),
                "hash-s2",
                "2026-01-01T00:00:00Z",
                "seed.jsonl",
                "seed",
                "2026-01-01T00:00:00Z",
            ],
        )

    raw_dir = tmp_path / "raw"
    run_dir = raw_dir / "runs" / "full-run"
    run_dir.mkdir(parents=True)
    (run_dir / "styles.jsonl").write_text(
        json.dumps({"id": "S1", "_modified_at": "2026-01-02T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "full-run",
                "mode": "full",
                "started_at": "2026-01-02T00:00:00Z",
                "endpoints": {"styles": {"file": "styles.jsonl", "is_delta": False}},
            }
        ),
        encoding="utf-8",
    )

    result = ingest_raw_dir(raw_dir, db_path, schemas={})

    assert result.records_deleted == 0
    assert result.records_hard_deleted == 1
    assert result.deleted_record_ids_by_endpoint == {"styles": ("S2",)}
    with sqlite3.connect(db_path) as conn:
        current_ids = [
            row[0]
            for row in conn.execute(
                "SELECT record_id FROM endpoint_records WHERE endpoint = ? ORDER BY record_id",
                ["styles"],
            ).fetchall()
        ]
        tombstone = conn.execute(
            """
            SELECT payload_json
            FROM endpoint_tombstones
            WHERE endpoint = ? AND record_id = ?
            """,
            ["styles", "S2"],
        ).fetchone()

    assert current_ids == ["S1"]
    assert tombstone is not None
    tombstone_payload = json.loads(tombstone[0])
    assert tombstone_payload["id"] == "S2"
    assert tombstone_payload["_centric_api_delete_type"] == "hard_delete"
