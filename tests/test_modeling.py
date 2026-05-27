from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from centric_api.cli import main
from centric_api.config import ConfigError
from centric_api.modeling import ModelDefinition, ModelOutput
from centric_api.modeling.registry import discover_models
from centric_api.modeling.runner import run_model
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


def test_model_run_fails_when_required_endpoint_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path):
        pass

    summary = run_model(db_path, _RequiresMissingEndpointModel())

    assert summary.status == "failed"
    assert summary.error_count == 1
    assert summary.issues[0].code == "missing_endpoint"
    with sqlite3.connect(db_path) as conn:
        output_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_missing_endpoint'"
        ).fetchone()
        run_row = conn.execute(
            "SELECT status, error_count FROM model_runs WHERE model_name = 'requires-missing'"
        ).fetchone()
    assert output_exists is None
    assert run_row == ("failed", 1)


def test_model_output_validation_preserves_existing_output_table(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute("CREATE TABLE model_bad_output (value TEXT)")
        conn.execute("INSERT INTO model_bad_output (value) VALUES ('existing')")

    with pytest.raises(ConfigError, match="at least one column"):
        run_model(db_path, _EmptyOutputModel())

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT value FROM model_bad_output").fetchall()
        run_count = conn.execute(
            "SELECT COUNT(*) FROM model_runs WHERE model_name = 'empty-output'"
        ).fetchone()[0]
    assert rows == [("existing",)]
    assert run_count == 0


def test_model_registry_rejects_duplicate_names(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    _write_named_model(models_dir / "one.py", "duplicate-model")
    _write_named_model(models_dir / "two.py", "duplicate-model")

    with pytest.raises(ConfigError, match="Duplicate model name"):
        discover_models(models_dir)


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


def _write_named_model(path: Path, name: str) -> None:
    path.write_text(
        f"""
from centric_api.modeling import ModelColumn, ModelDefinition, ModelOutput


class TestModel:
    definition = ModelDefinition(name={name!r}, title="Test", output_table="model_test")

    def check(self, ctx):
        return None

    def run(self, ctx):
        return ModelOutput(columns=(ModelColumn("value"),), rows=())


MODEL = TestModel()
""",
        encoding="utf-8",
    )


class _RequiresMissingEndpointModel:
    definition = ModelDefinition(
        name="requires-missing",
        title="Requires Missing",
        output_table="model_missing_endpoint",
        required_endpoints=("missing_endpoint",),
    )

    def check(self, _ctx) -> None:
        return None

    def run(self, _ctx) -> ModelOutput:
        raise AssertionError("run should not be called when required endpoints are missing")


class _EmptyOutputModel:
    definition = ModelDefinition(
        name="empty-output",
        title="Empty Output",
        output_table="model_bad_output",
    )

    def check(self, _ctx) -> None:
        return None

    def run(self, _ctx) -> ModelOutput:
        return ModelOutput(columns=(), rows=())


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
