from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from centric_api.load_config import load_load_config, select_load_job
from tests.helpers_load import _write_material_workbook


def test_private_load_workflow_module_dispatches(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    workflow_dir = home / "load" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "private_echo.py").write_text(
        """
from centric_api.load.generic import materialize_load, run_load


def materialize_private_echo_workflow(*args, **kwargs):
    return materialize_load(*args, **kwargs)


def run_private_echo_workflow(*args, **kwargs):
    return run_load(*args, **kwargs)
""",
        encoding="utf-8",
    )
    (home / "load.yml").write_text(
        """
version: 1

jobs:
  - name: private-echo
    title: Private Echo
    workflow: private_echo
    method: POST
    path: /v2/materials
    input:
      header_row: 1
    columns:
      code:
        header: Code
        type: text
        required: true
    body:
      code: code
""",
        encoding="utf-8",
    )
    workbook_path = tmp_path / "private-echo.xlsx"
    _write_material_workbook(workbook_path, headers=["Code"], rows=[["MAT-001"]])

    assert (
        main(
            [
                "load",
                "run",
                "private-echo",
                str(workbook_path),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["requests"] == 1
    assert payload["request_samples"][0]["path"] == "/v2/materials"

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
    assert any(
        line.split()[0] == "material-create" and "private" in line
        for line in list_output.splitlines()
        if line.split()
    )
    assert not any(
        line.split()[0] == "material-create" and "bundled" in line
        for line in list_output.splitlines()
        if line.split()
    )

    assert main(["load", "show", "material-create"]) == 0
    show_output = capsys.readouterr().out
    assert "Source:     private" in show_output
    assert f"Config:     {private_config}" in show_output
