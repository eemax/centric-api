from __future__ import annotations

from centric_api.load import run_load
from centric_api.load_config import load_load_config, select_load_job
from centric_api.store import connect
from tests.helpers_load import (
    _FakeAuthContext,
    _insert_record,
    _MixedAuthContext,
    _review_row,
    _write_material_workbook,
)


def test_load_retry_processes_failed_review_rows(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Name", "Material Type"],
        rows=[
            ["MAT-001", "Cotton Rib 240 GSM", "Fabric"],
            ["MAT-002", "Cotton Jersey 180 GSM", "Fabric"],
        ],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
    config = load_load_config()
    first = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=_MixedAuthContext(),
    )
    assert first.review_path is not None

    retry = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        first.review_path,
        sheet=None,
        limit=None,
        dry_run=True,
        yes=False,
        retry_statuses={"failed"},
    )

    assert retry.rows_scanned == 1
    assert retry.requests[0].row == 3
    assert retry.requests[0].body["code"] == "MAT-002"

def test_load_retry_review_clears_unprocessed_old_statuses(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Name", "Material Type"],
        rows=[
            ["MAT-001", "Cotton Rib 240 GSM", "Fabric"],
            ["MAT-002", "Cotton Jersey 180 GSM", "Fabric"],
        ],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
    config = load_load_config()
    first = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=_MixedAuthContext(),
    )
    assert first.review_path is not None

    retry = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        first.review_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        retry_statuses={"failed"},
        auth_ctx=_FakeAuthContext(),
    )

    assert retry.review_path is not None
    assert _review_row(retry.review_path, row_number=2)["_cent_load_status"] is None
    assert _review_row(retry.review_path, row_number=3)["_cent_load_status"] == "success"
