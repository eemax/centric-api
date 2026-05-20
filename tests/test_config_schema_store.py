from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from centric_api.config import load_fetcher_settings, runtime_home, runtime_path
from centric_api.fetcher import FetchError, get_expected_count, run_endpoint
from centric_api.models import CountSpec, EndpointSpec, FetcherConfig
from centric_api.schema import load_endpoint_schemas
from centric_api.store import connect, ingest_raw_dir


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


def test_connect_installs_dashboard_views(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
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

    assert {
        "dashboard_latest_fetch_runs",
        "dashboard_endpoint_state",
        "dashboard_recent_changes",
        "dashboard_actor_activity",
        "dashboard_download_jobs",
        "dashboard_bundle_runs",
        "dashboard_bundle_file_changes",
    } <= views


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
