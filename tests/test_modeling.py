from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from centric_api.cli import main
from centric_api.store import connect


def test_model_cli_runs_private_model(tmp_path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    _write_demo_model(models_dir / "demo_model.py")
    with connect(db_path) as conn:
        _insert_endpoint_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "Style One"},
        )

    assert main(["model", "--models-dir", str(models_dir), "list"]) == 0
    assert "demo-model" in capsys.readouterr().out

    assert (
        main(
            ["model", "--models-dir", str(models_dir), "check", "demo-model", "--db", str(db_path)]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Status:   ok" in output

    assert (
        main(
            [
                "model",
                "--models-dir",
                str(models_dir),
                "run",
                "demo-model",
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["rows"] == 1
    assert payload["metrics"] == {"ok_rows": 1, "error_rows": 0}

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT style_id, style_name FROM model_demo").fetchone()
        metrics_json = conn.execute(
            """
            SELECT metrics_json
            FROM model_runs
            WHERE model_name = 'demo-model' AND action = 'run'
            ORDER BY finished_at DESC
            LIMIT 1
            """
        ).fetchone()[0]
    assert row == ("S1", "Style One")
    assert json.loads(metrics_json) == {"ok_rows": 1, "error_rows": 0}


def _write_demo_model(path: Path) -> None:
    path.write_text(
        """
from centric_api.modeling import ModelColumn, ModelDefinition, ModelOutput


class DemoModel:
    definition = ModelDefinition(
        name="demo-model",
        title="Demo Model",
        output_table="model_demo",
        required_endpoints=("styles",),
    )

    def check(self, ctx):
        return None

    def run(self, ctx):
        rows = [
            {"style_id": record["id"], "style_name": record.get("node_name")}
            for record in ctx.records("styles")
        ]
        return ModelOutput(
            columns=(
                ModelColumn("style_id", "text"),
                ModelColumn("style_name", "text"),
            ),
            rows=tuple(rows),
            metrics={"ok_rows": len(rows), "error_rows": 0},
        )


MODEL = DemoModel()
""",
        encoding="utf-8",
    )


def _insert_endpoint_record(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    record_id: str,
    payload: dict[str, object],
) -> None:
    conn.execute(
        """
        INSERT INTO endpoint_records (
            endpoint, record_id, payload_json, payload_sha256,
            modified_at, source_file, source_run_id, ingested_at
        )
        VALUES (?, ?, json(?), ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            record_id,
            json.dumps(payload),
            f"hash-{endpoint}-{record_id}",
            None,
            "test.jsonl",
            "test-run",
            "2026-01-01T00:00:00Z",
        ],
    )
