from __future__ import annotations

from pathlib import Path

import pytest

import centric_api.load as load_module
from centric_api.load import run_load
from centric_api.load_config import load_load_config, select_load_job
from centric_api.store import connect
from tests.helpers_load import (
    _FailingAuthContext,
    _FakeAuthContext,
    _insert_record,
    _review_row,
    _write_material_workbook,
)


def test_load_run_emits_send_progress(tmp_path, monkeypatch) -> None:
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
    config = load_load_config()
    events: list[dict[str, object]] = []

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=_FakeAuthContext(),
        progress_callback=events.append,
    )

    assert result.success_count == 1
    assert result.review_path is not None
    send_events = [event for event in events if event["event"] == "load_send"]
    assert send_events[0]["index"] == 1
    assert send_events[0]["total"] == 1
    assert send_events[0]["status_code"] == 201
    review_row = _review_row(result.review_path)
    assert review_row["_cent_load_status"] == "success"
    assert review_row["_cent_load_status_code"] == 201
    assert review_row["_cent_load_response_id"] == "created"


def test_load_run_records_request_exceptions_as_failures(tmp_path, monkeypatch) -> None:
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
    config = load_load_config()

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=_FailingAuthContext(),
    )

    assert result.success_count == 0
    assert result.failure_count == 1
    assert result.responses[0].status_code == 0
    assert result.responses[0].body == {
        "error": "connection dropped",
        "type": "RuntimeError",
    }
    assert result.review_path is not None
    review_row = _review_row(result.review_path)
    assert review_row["_cent_load_status"] == "failed"
    assert review_row["_cent_load_status_code"] == 0
    assert review_row["_cent_load_message"] == "connection dropped"
    assert (result.run_dir / "responses.jsonl").is_file()
    assert (result.run_dir / "summary.json").is_file()


def test_load_review_workbook_escapes_formula_like_messages(tmp_path, monkeypatch) -> None:
    class FormulaFailureAuth:
        base_url = "https://example.test"

        def request(self, method: str, url: str, *, json_body: object) -> object:
            return FormulaFailureResponse()

    class FormulaFailureResponse:
        status_code = 400
        text = '{"message":"=bad formula"}'

        def json(self) -> dict[str, str]:
            return {"message": "=bad formula"}

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
    config = load_load_config()

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=FormulaFailureAuth(),
    )

    assert result.review_path is not None
    assert _review_row(result.review_path)["_cent_load_message"] == "'=bad formula"


def test_load_review_workbook_save_failure_removes_partial_file(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type"],
        rows=[["MAT-001", "Missing Type"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
    config = load_load_config()

    def fail_save(_workbook, filename) -> None:
        Path(filename).write_bytes(b"partial")
        raise RuntimeError("review save failed")

    monkeypatch.setattr("openpyxl.workbook.workbook.Workbook.save", fail_save)

    with pytest.raises(RuntimeError, match="review save failed"):
        run_load(
            db_path,
            config,
            select_load_job(config, "material-create"),
            workbook_path,
            sheet=None,
            limit=None,
            dry_run=False,
            yes=True,
            auth_ctx=None,
        )

    run_dirs = list((home / "load" / "runs").iterdir())
    assert len(run_dirs) == 1
    assert not (run_dirs[0] / "review.xlsx").exists()
    assert not (run_dirs[0] / ".review.xlsx.tmp").exists()


def test_load_jsonl_write_preserves_existing_file_when_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "requests.jsonl"
    output_path.write_text("existing\n", encoding="utf-8")

    def fail_dumps(*_args, **_kwargs):
        raise RuntimeError("json failed")

    monkeypatch.setattr(load_module.json, "dumps", fail_dumps)

    with pytest.raises(RuntimeError, match="json failed"):
        load_module._write_jsonl(output_path, [{"row": 1}])

    assert output_path.read_text(encoding="utf-8") == "existing\n"
    assert not (tmp_path / ".requests.jsonl.tmp").exists()


def test_load_run_writes_validation_error_review(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type"],
        rows=[["MAT-001", "Missing Type"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
    config = load_load_config()

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=_FakeAuthContext(),
    )

    assert result.issues
    assert result.success_count == 0
    assert result.failure_count == 0
    assert result.review_path is not None
    review_row = _review_row(result.review_path)
    assert review_row["_cent_load_status"] == "validation_error"
    assert "was not found" in review_row["_cent_load_message"]


def test_load_run_with_only_validation_errors_does_not_require_auth(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type"],
        rows=[["MAT-001", "Missing Type"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
    config = load_load_config()

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=None,
    )

    assert result.requests == ()
    assert result.issues
    assert result.review_path is not None


def test_load_run_header_errors_do_not_write_empty_review_file(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Name"],
        rows=[["MAT-001", "Unused display name"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
    config = load_load_config()

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=None,
    )

    assert result.requests == ()
    assert [issue.code for issue in result.issues] == ["missing_required_header"]
    assert result.review_path is None


def test_load_run_sends_valid_rows_when_other_rows_have_validation_errors(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Type"],
        rows=[
            ["MAT-001", "Missing Type"],
            ["MAT-002", "Fabric"],
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

    result = run_load(
        db_path,
        config,
        select_load_job(config, "material-create"),
        workbook_path,
        sheet=None,
        limit=None,
        dry_run=False,
        yes=True,
        auth_ctx=_FakeAuthContext(),
    )

    assert result.success_count == 1
    assert result.failure_count == 0
    assert len(result.issues) == 1
    assert result.review_path is not None
    assert _review_row(result.review_path, row_number=2)["_cent_load_status"] == "validation_error"
    assert _review_row(result.review_path, row_number=3)["_cent_load_status"] == "success"
