from __future__ import annotations

import json

from centric_api.cli import main
from centric_api.load import (
    run_material_create_with_composition_and_quote_workflow,
    run_material_create_with_composition_workflow,
)
from centric_api.load_config import load_load_config, select_load_job
from tests.helpers_load import _review_row
from tests.helpers_load_workflows import (
    _MaterialCreateCompositionAuthContext,
    _MaterialCreateCompositionQuoteAuthContext,
    _seed_material_create_composition_cache,
    _seed_material_create_composition_quote_cache,
    _write_material_create_composition_quote_workbook,
    _write_material_create_composition_workbook,
)


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
    assert payload["request_samples"][1]["body"] == [{"composition": "COTTON", "percentage": 100}]


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
    assert review_row["_cent_load_request_path"] == ("/v2/materials/NEW-MAT/technical_compositions")
    assert "Material composition request failed" in review_row["_cent_load_message"]
