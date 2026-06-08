from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from centric_api.load import run_style_bom_workflow
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


def test_style_bom_load_dry_run_plans_header_sections_and_lines(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines.xlsx"
    _write_style_bom_workbook(workbook_path)
    _seed_style_bom_load_cache(db_path)

    assert (
        main(
            [
                "load",
                "run",
                "style-bom-load",
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
    assert payload["requests"] == 5
    assert payload["request_samples"][0]["path"] == "/v2/styles/S1/data_sheets/apparel_boms"
    assert payload["request_samples"][0]["body"] == {
        "description": "Main production BOM",
        "node_name": "Main BOM",
        "subtype": "BST1",
    }
    assert payload["request_samples"][1]["path"] == (
        "/v2/apparel_bom_revisions/DRY-RUN-REVISION/"
        "owned_sections/bom_section_definition"
    )
    assert payload["request_samples"][1]["body"] == {"node_name": "Fabrics"}


def test_style_bom_load_runs_chained_requests(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines.xlsx"
    _write_style_bom_workbook(workbook_path)
    _seed_style_bom_load_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "style-bom-load")
    auth = _StyleBomAuthContext()

    result = run_style_bom_workflow(
        db_path,
        config,
        job,
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=auth,
    )

    assert result.failure_count == 0
    assert not result.issues
    assert result.request_count == 5
    assert auth.calls == [
        (
            "POST",
            "https://example.test/api/v2/styles/S1/data_sheets/apparel_boms",
            {"description": "Main production BOM", "node_name": "Main BOM", "subtype": "BST1"},
        ),
        (
            "POST",
            "https://example.test/api/v2/apparel_bom_revisions/REV1/"
            "owned_sections/bom_section_definition",
            {"node_name": "Fabrics"},
        ),
        (
            "POST",
            "https://example.test/api/v2/apparel_bom_revisions/REV1/"
            "owned_sections/bom_section_definition",
            {"node_name": "Trims"},
        ),
        (
            "POST",
            "https://example.test/api/v2/apparel_bom_revisions/REV1/items/part_materials",
            {"actual": "M1", "ds_section": "SEC-Fabrics", "pm_id": "G2", "qty_default": 0.05},
        ),
        (
            "POST",
            "https://example.test/api/v2/apparel_bom_revisions/REV1/items/part_materials",
            {"actual": "M2", "ds_section": "SEC-Trims", "pm_id": "G3", "qty_default": 2},
        ),
    ]


def test_style_bom_load_matches_headers_when_columns_are_shuffled(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines-shuffled.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=[
            "Material Code",
            "Quantity",
            "PM ID",
            "Section",
            "Subtype",
            "Description",
            "BOM Name",
            "Style",
            "Season",
        ],
        rows=[
            [
                "MAT-001",
                0.05,
                "G2",
                "Fabrics",
                "Production",
                "Main production BOM",
                "Main BOM",
                "ST-001",
                "SS26",
            ],
        ],
    )
    _seed_style_bom_load_cache(db_path)

    assert (
        main(
            [
                "load",
                "run",
                "style-bom-load",
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
    assert payload["requests"] == 3
    assert payload["request_samples"][0]["path"] == "/v2/styles/S1/data_sheets/apparel_boms"
    assert payload["request_samples"][2]["body"] == {
        "actual": "M1",
        "ds_section": "DRY-RUN-SECTION-Fabrics",
        "pm_id": "G2",
        "qty_default": 0.05,
    }


def test_style_bom_load_marks_line_failures_as_row_issues(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines.xlsx"
    _write_style_bom_workbook(workbook_path)
    _seed_style_bom_load_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "style-bom-load")
    auth = _StyleBomAuthContext(fail_lines=True)

    result = run_style_bom_workflow(
        db_path,
        config,
        job,
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=auth,
    )

    assert result.failure_count == 2
    assert result.request_count == 5
    assert [issue.code for issue in result.issues] == [
        "bom_line_create_failed",
        "bom_line_create_failed",
    ]
    assert [issue.row for issue in result.issues] == [2, 3]


def test_style_bom_load_rejects_ad_hoc_sections(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines.xlsx"
    _write_style_bom_workbook(workbook_path)
    _seed_style_bom_load_cache(db_path, section_flags={"Fabrics": {"ad_hoc": True}})

    assert (
        main(
            [
                "load",
                "check",
                "style-bom-load",
                str(workbook_path),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"][0]["code"] == "bom_section_not_found"


def test_style_bom_load_rejects_inactive_sections(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines.xlsx"
    _write_style_bom_workbook(workbook_path)
    _seed_style_bom_load_cache(db_path, section_flags={"Fabrics": {"active": False}})

    assert (
        main(
            [
                "load",
                "check",
                "style-bom-load",
                str(workbook_path),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"][0]["code"] == "bom_section_not_found"


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


def _write_style_bom_workbook(path: Path) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Season",
            "Style",
            "BOM Name",
            "Description",
            "Subtype",
            "Section",
            "PM ID",
            "Quantity",
            "Material Code",
        ],
        rows=[
            [
                "SS26",
                "ST-001",
                "Main BOM",
                "Main production BOM",
                "Production",
                "Fabrics",
                "G2",
                0.05,
                "MAT-001",
            ],
            [
                "SS26",
                "ST-001",
                "Main BOM",
                "Main production BOM",
                "Production",
                "Trims",
                "G3",
                2,
                "MAT-002",
            ],
        ],
    )


def _seed_style_bom_load_cache(
    db_path: Path,
    *,
    section_flags: dict[str, dict[str, object]] | None = None,
) -> None:
    section_flags = section_flags or {}
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SE1",
            payload={"id": "SE1", "node_name": "SS26"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "ST-001", "parent_season": "SE1"},
        )
        _insert_record(
            conn,
            endpoint="bom_subtypes",
            record_id="BST1",
            payload={"id": "BST1", "node_name": "Production"},
        )
        _insert_record(
            conn,
            endpoint="bom_sections",
            record_id="BS1",
            payload={
                "id": "BS1",
                "node_name": "Fabrics",
                "active": True,
                "ad_hoc": False,
                **section_flags.get("Fabrics", {}),
            },
        )
        _insert_record(
            conn,
            endpoint="bom_sections",
            record_id="BS2",
            payload={
                "id": "BS2",
                "node_name": "Trims",
                "active": True,
                "ad_hoc": False,
                **section_flags.get("Trims", {}),
            },
        )
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M2",
            payload={"id": "M2", "code": "MAT-002"},
        )


class _StyleBomAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_lines: bool = False) -> None:
        self.fail_lines = fail_lines
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if url.endswith("/data_sheets/apparel_boms"):
            return _JsonResponse(
                201,
                {
                    "id": "BOM1",
                    "latest_revision": "REV1",
                    "current_revision": "REV1",
                },
            )
        if url.endswith("/owned_sections/bom_section_definition"):
            assert isinstance(json_body, dict)
            return _JsonResponse(201, {"id": f"SEC-{json_body['node_name']}"})
        if url.endswith("/items/part_materials"):
            if self.fail_lines:
                return _JsonResponse(422, {"message": "line rejected"})
            return _JsonResponse(201, {"id": "LINE"})
        return _JsonResponse(404, {"message": "unexpected url"})


class _JsonResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload
