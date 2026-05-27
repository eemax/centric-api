from __future__ import annotations

from centric_api.load import materialize_load
from centric_api.load_config import load_load_config, parse_load_config, select_load_job
from centric_api.store import connect
from tests.helpers_load import _insert_record, _write_material_workbook


def test_load_check_resolves_material_create_refs_and_alias_headers(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material Code", "Material", "Type", "Desc"],
        rows=[["MAT-001", "Cotton Rib 240 GSM", "Fabric", "Main body fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT0",
            payload={"id": "MT0", "node_name": "FABRIC", "available": False},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-create"),
        workbook_path,
    )

    assert result.valid_rows == 1
    assert result.issues == ()
    assert result.requests[0].body == {
        "code": "MAT-001",
        "node_name": "Cotton Rib 240 GSM",
        "product_type": "MT1",
        "description": "Main body fabric",
    }

def test_ref_indexes_keep_same_ref_with_different_filters(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Active Type", "Inactive Type"],
        rows=[["MAT-001", "Fabric", "Fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT0",
            payload={"id": "MT0", "node_name": "Fabric", "available": False},
        )
    config = parse_load_config(
        {
            "version": 1,
            "jobs": [
                {
                    "name": "filtered-refs",
                    "method": "POST",
                    "path": "/v2/materials",
                    "columns": {
                        "code": {"header": "Code", "required": True},
                        "active_type": {
                            "header": "Active Type",
                            "type": "ref",
                            "required": True,
                            "resolve": {
                                "endpoint": "material_types",
                                "match": "node_name",
                                "output": "id",
                                "filters": {"available": True},
                            },
                        },
                        "inactive_type": {
                            "header": "Inactive Type",
                            "type": "ref",
                            "required": True,
                            "resolve": {
                                "endpoint": "material_types",
                                "match": "node_name",
                                "output": "id",
                                "filters": {"available": False},
                            },
                        },
                    },
                    "body": {
                        "code": "code",
                        "active": "active_type",
                        "inactive": "inactive_type",
                    },
                }
            ],
        },
        path=tmp_path / "load.yml",
    )

    result = materialize_load(
        db_path,
        select_load_job(config, "filtered-refs"),
        workbook_path,
    )

    assert result.issues == ()
    assert result.requests[0].body == {
        "code": "MAT-001",
        "active": "MT1",
        "inactive": "MT0",
    }

def test_load_check_fails_ambiguous_alias_headers(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Material Name", "Name", "Material Type"],
        rows=[["MAT-001", "Cotton Rib 240 GSM", "Duplicate", "Fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-create"),
        workbook_path,
    )

    assert [issue.code for issue in result.issues] == ["ambiguous_header"]
    assert result.issues[0].column == "node_name"
