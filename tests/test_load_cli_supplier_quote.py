from __future__ import annotations

import json

from centric_api.cli import main
from centric_api.load import run_material_supplier_quote_workflow, run_style_supplier_quote_workflow
from centric_api.load_config import load_load_config, select_load_job
from tests.helpers_load import _write_material_workbook
from tests.helpers_load_workflows import (
    _seed_material_supplier_quote_cache,
    _seed_style_supplier_quote_cache,
    _StyleSupplierQuoteAuthContext,
    _write_material_supplier_quote_workbook,
    _write_style_supplier_quote_workbook,
)


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
    assert payload["request_samples"][2]["path"] == ("/v2/supplier_item_revisions/DRY-RUN-REVISION")


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
    assert payload["request_samples"][2]["path"] == ("/v2/supplier_item_revisions/DRY-RUN-REVISION")


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
