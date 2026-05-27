from __future__ import annotations

import json
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
        headers=["Code", "Material Name", "Material Type"],
        rows=[["MAT-001", "Cotton Rib 240 GSM", "Fabric"]],
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

def test_load_cli_reports_human_progress(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Name", "Material Type"],
        rows=[["MAT-001", "Cotton Rib 240 GSM", "Fabric"]],
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
        headers=["Code", "Material Name", "Material Type"],
        rows=[["MAT-001", "Cotton Rib 240 GSM", "Fabric"]],
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
