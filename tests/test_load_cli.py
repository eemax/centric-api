from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from centric_api.load import (
    run_material_create_with_composition_and_quote_workflow,
    run_material_create_with_composition_workflow,
    run_material_supplier_quote_workflow,
    run_style_bom_workflow,
    run_style_supplier_quote_workflow,
)
from centric_api.load_config import load_load_config, select_load_job
from centric_api.store import connect
from tests.helpers_load import _insert_record, _review_row, _write_material_workbook


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


def test_material_create_with_composition_dry_run_plans_chain(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials-with-composition.xlsx"
    _write_material_create_composition_workbook(workbook_path)
    _seed_material_create_composition_cache(db_path)

    assert (
        main(
            [
                "load",
                "run",
                "material-create-with-composition",
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
    assert payload["requests"] == 2
    assert payload["request_samples"][0]["path"] == "/v2/materials"
    assert payload["request_samples"][0]["body"] == {
        "code": "MAT-001",
        "description": "Test fabric",
        "product_type": "MT1",
    }
    assert payload["request_samples"][1]["path"] == (
        "/v2/materials/DRY-RUN-MATERIAL/technical_compositions"
    )
    assert payload["request_samples"][1]["body"] == [
        {"composition": "COTTON", "percentage": 100}
    ]


def test_material_create_with_composition_runs_chained_requests(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials-with-composition.xlsx"
    _write_material_create_composition_workbook(workbook_path)
    _seed_material_create_composition_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "material-create-with-composition")
    auth = _MaterialCreateCompositionAuthContext()

    result = run_material_create_with_composition_workflow(
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
    assert result.request_count == 2
    assert auth.calls == [
        (
            "POST",
            "https://example.test/api/v2/materials",
            {"code": "MAT-001", "description": "Test fabric", "product_type": "MT1"},
        ),
        (
            "POST",
            "https://example.test/api/v2/materials/NEW-MAT/technical_compositions",
            [{"composition": "COTTON", "percentage": 100}],
        ),
    ]


def test_material_create_with_composition_marks_create_failure(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials-with-composition.xlsx"
    _write_material_create_composition_workbook(workbook_path)
    _seed_material_create_composition_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "material-create-with-composition")
    auth = _MaterialCreateCompositionAuthContext(fail_material=True)

    result = run_material_create_with_composition_workflow(
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

    assert result.failure_count == 1
    assert result.error_rows == 1
    assert result.request_count == 1
    assert [issue.code for issue in result.issues] == ["material_create_failed"]
    assert [issue.row for issue in result.issues] == [2]


def test_material_create_with_composition_and_quote_dry_run_plans_chain(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "material-full-create.xlsx"
    _write_material_create_composition_quote_workbook(workbook_path)
    _seed_material_create_composition_quote_cache(db_path)

    assert (
        main(
            [
                "load",
                "run",
                "material-create-with-composition-and-quote",
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
    assert payload["requests"] == 6
    assert payload["request_samples"][0]["path"] == "/v2/materials"
    assert payload["request_samples"][0]["body"] == {
        "code": "MAT-001",
        "description": "Test fabric",
        "product_type": "MT1",
    }
    assert payload["request_samples"][1]["path"] == (
        "/v2/materials/DRY-RUN-MATERIAL/technical_compositions"
    )
    assert payload["request_samples"][2]["path"] == (
        "/v2/materials/DRY-RUN-MATERIAL/product_sources"
    )


def test_material_create_with_composition_and_quote_runs_chained_requests(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "material-full-create.xlsx"
    _write_material_create_composition_quote_workbook(workbook_path)
    _seed_material_create_composition_quote_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "material-create-with-composition-and-quote")
    auth = _MaterialCreateCompositionQuoteAuthContext()

    result = run_material_create_with_composition_and_quote_workflow(
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
    assert result.request_count == 6
    assert auth.calls == [
        (
            "POST",
            "https://example.test/api/v2/materials",
            {"code": "MAT-001", "description": "Test fabric", "product_type": "MT1"},
        ),
        (
            "POST",
            "https://example.test/api/v2/materials/NEW-MAT/technical_compositions",
            [{"composition": "COTTON", "percentage": 100}],
        ),
        (
            "POST",
            "https://example.test/api/v2/materials/NEW-MAT/product_sources",
            {"agent": "A1", "supplier": "SUP1"},
        ),
        (
            "POST",
            "https://example.test/api/v2/product_sources/PS1/supplier_items",
            {"description": "Primary material quote", "node_name": "Main Material Quote"},
        ),
        (
            "PUT",
            "https://example.test/api/v2/supplier_item_revisions/REV1",
            {"quote_factory": "F1"},
        ),
        (
            "PUT",
            "https://example.test/api/v2/materials/NEW-MAT",
            {"default_quote": "SQ1"},
        ),
    ]


def test_material_create_with_composition_and_quote_review_keeps_partial_failure(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "material-full-create.xlsx"
    _write_material_create_composition_quote_workbook(workbook_path)
    _seed_material_create_composition_quote_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "material-create-with-composition-and-quote")
    auth = _MaterialCreateCompositionQuoteAuthContext(fail_composition=True)

    result = run_material_create_with_composition_and_quote_workflow(
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

    assert result.failure_count == 1
    assert [issue.code for issue in result.issues] == ["material_composition_create_failed"]
    assert result.review_path is not None
    review_row = _review_row(result.review_path)
    assert review_row["_cent_load_status"] == "failed"
    assert review_row["_cent_load_status_code"] == 422
    assert review_row["_cent_load_request_path"] == (
        "/v2/materials/NEW-MAT/technical_compositions"
    )
    assert "Material composition request failed" in review_row["_cent_load_message"]


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


def test_style_supplier_quote_load_dry_run_plans_chain(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes.xlsx"
    _write_style_supplier_quote_workbook(workbook_path)
    _seed_style_supplier_quote_cache(db_path)

    assert (
        main(
            [
                "load",
                "run",
                "style-supplier-quote-load",
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
    assert payload["request_samples"][0]["path"] == "/v2/styles/S1/product_sources"
    assert payload["request_samples"][0]["body"] == {"agent": "A1", "supplier": "SUP1"}
    assert payload["request_samples"][1]["path"] == (
        "/v2/product_sources/DRY-RUN-PRODUCT-SOURCE/supplier_items"
    )
    assert payload["request_samples"][2]["path"] == (
        "/v2/supplier_item_revisions/DRY-RUN-REVISION"
    )


def test_material_supplier_quote_load_dry_run_plans_chain(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "material-supplier-quotes.xlsx"
    _write_material_supplier_quote_workbook(workbook_path)
    _seed_material_supplier_quote_cache(db_path)

    assert (
        main(
            [
                "load",
                "run",
                "material-supplier-quote-load",
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
    assert payload["request_samples"][0]["path"] == "/v2/materials/M1/product_sources"
    assert payload["request_samples"][0]["body"] == {"agent": "A1", "supplier": "SUP1"}
    assert payload["request_samples"][1]["path"] == (
        "/v2/product_sources/DRY-RUN-PRODUCT-SOURCE/supplier_items"
    )
    assert payload["request_samples"][2]["path"] == (
        "/v2/supplier_item_revisions/DRY-RUN-REVISION"
    )


def test_material_supplier_quote_load_runs_chained_requests(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "material-supplier-quotes.xlsx"
    _write_material_supplier_quote_workbook(workbook_path)
    _seed_material_supplier_quote_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "material-supplier-quote-load")
    auth = _StyleSupplierQuoteAuthContext()

    result = run_material_supplier_quote_workflow(
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
    assert result.request_count == 4
    assert auth.calls == [
        (
            "POST",
            "https://example.test/api/v2/materials/M1/product_sources",
            {"agent": "A1", "supplier": "SUP1"},
        ),
        (
            "POST",
            "https://example.test/api/v2/product_sources/PS1/supplier_items",
            {"description": "Primary material quote", "node_name": "Main Material Quote"},
        ),
        (
            "PUT",
            "https://example.test/api/v2/supplier_item_revisions/REV1",
            {"quote_factory": "F1"},
        ),
        (
            "PUT",
            "https://example.test/api/v2/materials/M1",
            {"default_quote": "SQ1"},
        ),
    ]


def test_style_supplier_quote_load_runs_chained_requests(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes.xlsx"
    _write_style_supplier_quote_workbook(workbook_path)
    _seed_style_supplier_quote_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "style-supplier-quote-load")
    auth = _StyleSupplierQuoteAuthContext()

    result = run_style_supplier_quote_workflow(
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
    assert result.request_count == 4
    assert auth.calls == [
        (
            "POST",
            "https://example.test/api/v2/styles/S1/product_sources",
            {"agent": "A1", "supplier": "SUP1"},
        ),
        (
            "POST",
            "https://example.test/api/v2/product_sources/PS1/supplier_items",
            {"description": "Primary supplier quote", "node_name": "Main Quote"},
        ),
        (
            "PUT",
            "https://example.test/api/v2/supplier_item_revisions/REV1",
            {"quote_factory": "F1"},
        ),
        (
            "PUT",
            "https://example.test/api/v2/styles/S1",
            {"production_quote": "SQ1"},
        ),
    ]


def test_style_supplier_quote_load_marks_product_source_failure(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes.xlsx"
    _write_style_supplier_quote_workbook(workbook_path)
    _seed_style_supplier_quote_cache(db_path)
    config = load_load_config()
    job = select_load_job(config, "style-supplier-quote-load")
    auth = _StyleSupplierQuoteAuthContext(fail_product_source=True)

    result = run_style_supplier_quote_workflow(
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

    assert result.failure_count == 1
    assert result.error_rows == 1
    assert result.request_count == 1
    assert [issue.code for issue in result.issues] == ["product_source_create_failed"]
    assert [issue.row for issue in result.issues] == [2]


def test_style_supplier_quote_load_rejects_unlinked_agent(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes.xlsx"
    _write_style_supplier_quote_workbook(workbook_path)
    _seed_style_supplier_quote_cache(db_path, supplier_agents=())

    assert (
        main(
            [
                "load",
                "check",
                "style-supplier-quote-load",
                str(workbook_path),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"][0]["code"] == "agent_not_linked_to_supplier"


def test_style_supplier_quote_load_omits_blank_agent(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes-no-agent.xlsx"
    _write_style_supplier_quote_workbook(workbook_path, agent="")
    _seed_style_supplier_quote_cache(db_path, supplier_agents=())

    assert (
        main(
            [
                "load",
                "run",
                "style-supplier-quote-load",
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
    assert payload["request_samples"][0]["path"] == "/v2/styles/S1/product_sources"
    assert payload["request_samples"][0]["body"] == {"supplier": "SUP1"}


def test_style_supplier_quote_load_allows_missing_agent_header(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes-missing-agent.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=[
            "Season",
            "Style",
            "Supplier",
            "Supplier Item",
            "Description",
            "Quote Factory",
            "Set Production Quote",
        ],
        rows=[
            [
                "SS26",
                "ST-001",
                "Primary Supplier",
                "Main Quote",
                "Primary supplier quote",
                "Primary Factory",
                "Yes",
            ],
        ],
    )
    _seed_style_supplier_quote_cache(db_path, supplier_agents=())

    assert (
        main(
            [
                "load",
                "check",
                "style-supplier-quote-load",
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


def test_style_supplier_quote_load_rejects_unlinked_factory(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes.xlsx"
    _write_style_supplier_quote_workbook(workbook_path)
    _seed_style_supplier_quote_cache(db_path, factory_suppliers=("OTHER",))

    assert (
        main(
            [
                "load",
                "check",
                "style-supplier-quote-load",
                str(workbook_path),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"][0]["code"] == "factory_not_linked_to_supplier"


def test_style_supplier_quote_load_allows_blank_factory_without_factory_cache(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "style-supplier-quotes.xlsx"
    _write_style_supplier_quote_workbook(workbook_path, quote_factory="")
    _seed_style_supplier_quote_cache(db_path, include_factory=False)

    assert (
        main(
            [
                "load",
                "run",
                "style-supplier-quote-load",
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
    assert all("supplier_item_revisions" not in item["path"] for item in payload["request_samples"])


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


def _write_material_create_composition_workbook(path: Path) -> None:
    _write_material_workbook(
        path,
        headers=["Code", "Product Type", "Description", "Composition"],
        rows=[["MAT-001", "Fabric", "Test fabric", "100% Cotton"]],
    )


def _write_material_create_composition_quote_workbook(path: Path) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Code",
            "Product Type",
            "Material Description",
            "Composition",
            "Supplier",
            "Agent",
            "Supplier Item",
            "Quote Description",
            "Quote Factory",
            "Set Default Quote",
        ],
        rows=[
            [
                "MAT-001",
                "Fabric",
                "Test fabric",
                "100% Cotton",
                "Primary Supplier",
                "Primary Agent",
                "Main Material Quote",
                "Primary material quote",
                "Primary Factory",
                "Yes",
            ],
        ],
    )


def _seed_material_create_composition_cache(db_path: Path) -> None:
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="COTTON",
            payload={"id": "COTTON", "node_name": "Cotton", "ok_for_material": True},
        )


def _seed_material_create_composition_quote_cache(db_path: Path) -> None:
    _seed_material_create_composition_cache(db_path)
    _seed_style_supplier_quote_cache(db_path)


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


def _write_style_supplier_quote_workbook(
    path: Path,
    *,
    agent: str = "Primary Agent",
    quote_factory: str = "Primary Factory",
) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Season",
            "Style",
            "Supplier",
            "Agent",
            "Supplier Item",
            "Description",
            "Quote Factory",
            "Set Production Quote",
        ],
        rows=[
            [
                "SS26",
                "ST-001",
                "Primary Supplier",
                agent,
                "Main Quote",
                "Primary supplier quote",
                quote_factory,
                "Yes",
            ],
        ],
    )


def _write_material_supplier_quote_workbook(
    path: Path,
    *,
    agent: str = "Primary Agent",
    quote_factory: str = "Primary Factory",
) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Material Code",
            "Supplier",
            "Agent",
            "Supplier Item",
            "Description",
            "Quote Factory",
            "Set Default Quote",
        ],
        rows=[
            [
                "MAT-001",
                "Primary Supplier",
                agent,
                "Main Material Quote",
                "Primary material quote",
                quote_factory,
                "Yes",
            ],
        ],
    )


def _seed_material_supplier_quote_cache(
    db_path: Path,
    *,
    supplier_agents: tuple[str, ...] = ("A1",),
    factory_suppliers: tuple[str, ...] = ("SUP1",),
    include_factory: bool = True,
) -> None:
    _seed_style_supplier_quote_cache(
        db_path,
        supplier_agents=supplier_agents,
        factory_suppliers=factory_suppliers,
        include_factory=include_factory,
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )


def _seed_style_supplier_quote_cache(
    db_path: Path,
    *,
    supplier_agents: tuple[str, ...] = ("A1",),
    factory_suppliers: tuple[str, ...] = ("SUP1",),
    include_factory: bool = True,
) -> None:
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
            endpoint="suppliers",
            record_id="SUP1",
            payload={
                "id": "SUP1",
                "node_name": "Primary Supplier",
                "supplier_number": "SUP-001",
                "is_supplier": True,
                "is_agent": False,
                "all_agents": {str(index): value for index, value in enumerate(supplier_agents)},
            },
        )
        _insert_record(
            conn,
            endpoint="suppliers",
            record_id="A1",
            payload={
                "id": "A1",
                "node_name": "Primary Agent",
                "supplier_number": "AG-001",
                "is_supplier": False,
                "is_agent": True,
            },
        )
        if include_factory:
            _insert_record(
                conn,
                endpoint="factories",
                record_id="F1",
                payload={
                    "id": "F1",
                    "node_name": "Primary Factory",
                    "supplier_number": "FAC-001",
                    "suppliers": {
                        str(index): value for index, value in enumerate(factory_suppliers)
                    },
                },
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
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


class _MaterialCreateCompositionAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_material: bool = False) -> None:
        self.fail_material = fail_material
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if url.endswith("/materials"):
            if self.fail_material:
                return _JsonResponse(422, {"message": "material rejected"})
            return _JsonResponse(201, {"id": "NEW-MAT"})
        if url.endswith("/technical_compositions"):
            return _JsonResponse(201, [{"id": "COMP1"}])
        return _JsonResponse(404, {"message": "unexpected url"})


class _MaterialCreateCompositionQuoteAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_composition: bool = False) -> None:
        self.fail_composition = fail_composition
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if method == "POST" and url.endswith("/materials"):
            return _JsonResponse(201, {"id": "NEW-MAT"})
        if url.endswith("/technical_compositions"):
            if self.fail_composition:
                return _JsonResponse(422, {"message": "composition rejected"})
            return _JsonResponse(201, [{"id": "COMP1"}])
        if url.endswith("/product_sources"):
            return _JsonResponse(201, {"id": "PS1"})
        if url.endswith("/supplier_items"):
            return _JsonResponse(
                201,
                {
                    "id": "SQ1",
                    "latest_revision": "REV1",
                    "current_revision": "REV1",
                },
            )
        if url.endswith("/supplier_item_revisions/REV1"):
            return _JsonResponse(200, {"id": "REV1", "quote_factory": "F1"})
        if method == "PUT" and url.endswith("/materials/NEW-MAT"):
            return _JsonResponse(200, {"id": "NEW-MAT", "default_quote": "SQ1"})
        return _JsonResponse(404, {"message": "unexpected url"})


class _StyleSupplierQuoteAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_product_source: bool = False) -> None:
        self.fail_product_source = fail_product_source
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if url.endswith("/product_sources"):
            if self.fail_product_source:
                return _JsonResponse(422, {"message": "product source rejected"})
            return _JsonResponse(201, {"id": "PS1"})
        if url.endswith("/supplier_items"):
            return _JsonResponse(
                201,
                {
                    "id": "SQ1",
                    "latest_revision": "REV1",
                    "current_revision": "REV1",
                },
            )
        if url.endswith("/supplier_item_revisions/REV1"):
            return _JsonResponse(200, {"id": "REV1", "quote_factory": "F1"})
        if url.endswith("/styles/S1"):
            return _JsonResponse(200, {"id": "S1", "production_quote": "SQ1"})
        if url.endswith("/materials/M1"):
            return _JsonResponse(200, {"id": "M1", "default_quote": "SQ1"})
        return _JsonResponse(404, {"message": "unexpected url"})
