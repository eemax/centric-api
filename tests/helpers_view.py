from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from centric_api.store import connect


def _view_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
output_dir: exports
options:
  sheet_name: BOM Lines
views:
  - name: bom-lines
    title: BOM Lines
    root:
      endpoint: bom_lines
      as: bom_line
    joins:
      - as: style
        endpoint: styles
        from: bom_line.style
        to: id
        relationship: one
      - as: colorway
        endpoint: colorways
        from: bom_line.colorway
        to: id
        relationship: one
      - as: season
        endpoint: seasons
        from: style.season
        to: id
        relationship: one
      - as: supplier
        endpoint: suppliers
        from: style.supplier
        to: id
        relationship: one
    columns:
      - header: BOM Line ID
        path: bom_line.id
      - header: Style
        path: style.node_name
      - header: Colorway
        path: colorway.node_name
      - header: Season
        path: season.node_name
      - header: Supplier
        path: supplier.node_name
      - header: Quantity
        path: bom_line.quantity
        type: number
        number_format: "0.00"
""",
        encoding="utf-8",
    )
    return config_path


def _seed_bom_line_view_records(db_path: Path) -> None:
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="bom_lines",
            record_id="BL1",
            payload={"id": "BL1", "style": "S1", "colorway": "C1", "quantity": "2.5"},
        )
        _insert_record(
            conn,
            endpoint="bom_lines",
            record_id="BL2",
            payload={"id": "BL2", "style": "S1", "colorway": "C1", "quantity": 1},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "node_name": "Linen Shirt",
                "season": "SE1",
                "supplier": "SUP1",
            },
        )
        _insert_record(
            conn,
            endpoint="colorways",
            record_id="C1",
            payload={"id": "C1", "node_name": "Ivory", "style": "S1"},
        )
        _insert_record(
            conn, endpoint="seasons", record_id="SE1", payload={"id": "SE1", "node_name": "SS26"}
        )
        _insert_record(
            conn,
            endpoint="suppliers",
            record_id="SUP1",
            payload={"id": "SUP1", "node_name": "Acme Mills"},
        )


def _insert_record(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    record_id: str,
    payload: dict,
) -> None:
    payload_json = json.dumps(payload, sort_keys=True)
    conn.execute(
        """
        INSERT INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256, modified_at,
            source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            record_id,
            payload_json,
            f"hash-{endpoint}-{record_id}",
            payload.get("_modified_at"),
            f"{endpoint}.jsonl",
            "test-run",
            "2026-01-01T00:00:00Z",
        ],
    )
