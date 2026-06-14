from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from centric_api.schema import load_endpoint_schemas
from centric_api.store import connect, ingest_raw_dir


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
