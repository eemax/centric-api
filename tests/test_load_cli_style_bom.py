from __future__ import annotations

import json

from centric_api.cli import main
from centric_api.load import run_style_bom_workflow
from centric_api.load_config import load_load_config, select_load_job
from tests.helpers_load import _write_material_workbook
from tests.helpers_load_workflows import (
    _seed_style_bom_load_cache,
    _StyleBomAuthContext,
    _write_style_bom_workbook,
)


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

def test_style_bom_load_omits_blank_pm_id(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines-no-pm-id.xlsx"
    _write_material_workbook(
        workbook_path,
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
                "",
                0.05,
                "MAT-001",
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
    assert payload["request_samples"][2]["body"] == {
        "actual": "M1",
        "ds_section": "DRY-RUN-SECTION-Fabrics",
        "qty_default": 0.05,
    }

def test_style_bom_load_allows_missing_pm_id_header(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines-missing-pm-id.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=[
            "Season",
            "Style",
            "BOM Name",
            "Description",
            "Subtype",
            "Section",
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
                0.05,
                "MAT-001",
            ],
        ],
    )
    _seed_style_bom_load_cache(db_path)

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
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid_rows"] == 1
    assert payload["issues"] == []

def test_style_bom_load_omits_blank_quantity(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines-no-quantity.xlsx"
    _write_material_workbook(
        workbook_path,
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
                "",
                "MAT-001",
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
    assert payload["request_samples"][2]["body"] == {
        "actual": "M1",
        "ds_section": "DRY-RUN-SECTION-Fabrics",
        "pm_id": "G2",
    }

def test_style_bom_load_normalizes_comma_decimal_quantity(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines-comma-quantity.xlsx"
    _write_material_workbook(
        workbook_path,
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
                "0,05",
                "MAT-001",
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
    assert payload["request_samples"][2]["body"] == {
        "actual": "M1",
        "ds_section": "DRY-RUN-SECTION-Fabrics",
        "pm_id": "G2",
        "qty_default": 0.05,
    }

def test_style_bom_load_allows_missing_quantity_header(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "bom-lines-missing-quantity.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=[
            "Season",
            "Style",
            "BOM Name",
            "Description",
            "Subtype",
            "Section",
            "PM ID",
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
                "MAT-001",
            ],
        ],
    )
    _seed_style_bom_load_cache(db_path)

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
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid_rows"] == 1
    assert payload["issues"] == []

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
    assert result.error_rows == 2
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
