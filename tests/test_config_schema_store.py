from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from centric_api.config import load_fetcher_settings, runtime_home, runtime_path
from centric_api.schema import load_endpoint_schemas
from centric_api.store import ingest_raw_dir


def test_runtime_paths_use_centric_api_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))

    assert runtime_home() == tmp_path / "home"
    assert runtime_path("raw") == tmp_path / "home" / "raw"


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
""",
        encoding="utf-8",
    )

    fetcher_cfg, auth_settings, endpoints = load_fetcher_settings(config)

    assert fetcher_cfg.output_dir == tmp_path / "home" / "raw"
    assert fetcher_cfg.checkpoint_dir == tmp_path / "home" / "checkpoints"
    assert auth_settings.env_file == tmp_path / "home" / "local.env"
    assert [endpoint.name for endpoint in endpoints] == ["styles"]


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
