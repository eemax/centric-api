from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from centric_api.load_config import load_load_config, select_load_job
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
        headers=["Code", "Material Type"],
        rows=[["MAT-001", "Fabric"]],
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
        headers=["Code", "Material Type"],
        rows=[
            ["MAT-001", "Fabric"],
            ["MAT-002", "Fabric"],
            ["MAT-003", "Fabric"],
            ["MAT-004", "Fabric"],
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


def test_private_load_job_overrides_bundled_job(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    private_config = home / "load.yml"
    private_config.write_text(
        """
version: 1
jobs:
  - name: material-create
    title: Private Material Create
    method: POST
    path: /v2/private-materials
    columns:
      code:
        header: Code
        required: true
    body:
      code: code
""",
        encoding="utf-8",
    )

    config = load_load_config()
    job = select_load_job(config, "material-create")

    assert config.paths == (Path("config/load.yml"), private_config)
    assert len([item for item in config.jobs if item.name == "material-create"]) == 1
    assert job.source == "private"
    assert job.source_path == private_config
    assert job.path == "/v2/private-materials"

    assert main(["load", "list"]) == 0
    list_output = capsys.readouterr().out
    assert "material-create" in list_output
    assert "private" in list_output
    assert "/v2/private-materials" in list_output
    assert "material-create              private" in list_output
    assert "material-create              bundled" not in list_output

    assert main(["load", "show", "material-create"]) == 0
    show_output = capsys.readouterr().out
    assert "Source:     private" in show_output
    assert f"Config:     {private_config}" in show_output


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
        headers=["Code", "Material Type"],
        rows=[["MAT-001", "Fabric"]],
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
        headers=["Code", "Material Type"],
        rows=[["MAT-001", "Fabric"]],
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
