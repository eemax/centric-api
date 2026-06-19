from __future__ import annotations

import json
import re
from pathlib import Path

from centric_api.cli import main
from centric_api.store import connect
from tests.helpers_load import _insert_record, _write_material_workbook


def test_load_cli_dry_run_writes_request_artifacts(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type", "Description"],
        rows=[["MAT-001", "Fabric", "Test fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )

    assert (
        main(
            [
                "load",
                "run",
                "material-create",
                str(workbook_path),
                "--db",
                str(db_path),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["requests"] == 1
    assert payload["request_samples"][0]["body"]["product_type"] == "MT1"
    assert re.fullmatch(
        r"material-create-\d{4}-\d{2}-\d{2}-\d{4}(?:-\d+)?",
        Path(payload["run_dir"]).name,
    )
    requests_path = Path(payload["run_dir"]) / "requests.jsonl"
    assert requests_path.is_file()
    assert payload["review_workbook"] is None
    request_record = json.loads(requests_path.read_text(encoding="utf-8").splitlines()[0])
    assert request_record["method"] == "POST"
    assert request_record["path"] == "/v2/materials"


def test_load_cli_json_request_samples_are_capped_at_three(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type", "Description"],
        rows=[
            ["MAT-001", "Fabric", "Test fabric 1"],
            ["MAT-002", "Fabric", "Test fabric 2"],
            ["MAT-003", "Fabric", "Test fabric 3"],
            ["MAT-004", "Fabric", "Test fabric 4"],
        ],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )

    assert (
        main(
            [
                "load",
                "run",
                "material-create",
                str(workbook_path),
                "--db",
                str(db_path),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["requests"] == 4
    assert len(payload["request_samples"]) == 3


def test_explicit_load_config_source_is_shown(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    explicit_config = tmp_path / "load.yml"
    explicit_config.write_text(
        """
version: 1
jobs:
  - name: explicit-job
    method: POST
    path: /v2/explicit
    columns:
      code:
        header: Code
        required: true
    body:
      code: code
""",
        encoding="utf-8",
    )

    assert main(["load", "--load-config", str(explicit_config), "show", "explicit-job"]) == 0
    show_output = capsys.readouterr().out
    assert "Source:     explicit" in show_output
    assert f"Config:     {explicit_config}" in show_output


def test_load_show_includes_value_sets(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    explicit_config = tmp_path / "load.yml"
    explicit_config.write_text(
        """
version: 1
jobs:
  - name: material-value-set
    method: POST
    path: /v2/materials
    columns:
      code:
        header: Code
        required: true
      fabric_type:
        header: Fabric Type
        value_set:
          name: materials.fabric_type
    body:
      code: code
      fabric_type: fabric_type
""",
        encoding="utf-8",
    )

    assert main(["load", "--load-config", str(explicit_config), "show", "material-value-set"]) == 0
    show_output = capsys.readouterr().out

    assert "values materials.fabric_type" in show_output


def test_load_cli_reports_human_progress(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type", "Description"],
        rows=[["MAT-001", "Fabric", "Test fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )

    assert (
        main(
            [
                "load",
                "run",
                "material-create",
                str(workbook_path),
                "--db",
                str(db_path),
                "--dry-run",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "[load] planning: job=material-create mode=dry-run" in captured.err
    assert "[load] artifacts:" in captured.err
    assert "Load dry run: material-create" in captured.out


def test_load_cli_json_suppresses_human_progress(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type", "Description"],
        rows=[["MAT-001", "Fabric", "Test fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )

    assert (
        main(
            [
                "load",
                "check",
                "material-create",
                str(workbook_path),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert captured.err == ""
