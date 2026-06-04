from __future__ import annotations

import pytest

from centric_api.config import ConfigError
from centric_api.load import materialize_load
from centric_api.load_config import load_load_config, parse_load_config, select_load_job
from centric_api.store import connect
from tests.helpers_load import (
    _insert_record,
    _write_material_workbook,
    _write_value_set_workbook,
)


def test_load_check_resolves_material_create_refs_and_alias_headers(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material Code", "Type", "Desc"],
        rows=[["MAT-001", "Fabric", "Main body fabric"]],
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
        "product_type": "MT1",
        "description": "Main body fabric",
    }


def _value_set_config(tmp_path):
    return parse_load_config(
        {
            "version": 1,
            "jobs": [
                {
                    "name": "material-value-set",
                    "method": "POST",
                    "path": "/v2/materials",
                    "columns": {
                        "code": {"header": "Code", "required": True},
                        "fabric_type": {
                            "header": "Fabric Type",
                            "required": True,
                            "value_set": {"name": "materials.fabric_type"},
                        },
                    },
                    "body": {"code": "code", "fabric_type": "fabric_type"},
                }
            ],
        },
        path=tmp_path / "load.yml",
    )


def test_load_value_set_canonicalizes_private_xlsx_values(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Fabric Type"],
        rows=[
            ["MAT-001", " jersey "],
            ["MAT-002", "mid layer"],
            ["MAT-003", "MID-LAYERS"],
        ],
    )
    _write_value_set_workbook(
        home / "load" / "value-sets" / "materials.fabric_type.xlsx",
        ["Jerseys", "Midlayers"],
    )
    config = _value_set_config(tmp_path)

    result = materialize_load(
        db_path,
        select_load_job(config, "material-value-set"),
        workbook_path,
    )

    assert result.issues == ()
    assert [request.body["fabric_type"] for request in result.requests] == [
        "Jerseys",
        "Midlayers",
        "Midlayers",
    ]


def test_load_value_set_fails_unknown_workbook_value(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Fabric Type"],
        rows=[["MAT-001", "Outerwear"]],
    )
    _write_value_set_workbook(
        home / "load" / "value-sets" / "materials.fabric_type.xlsx",
        ["Jerseys", "Midlayers"],
    )
    config = _value_set_config(tmp_path)

    result = materialize_load(
        db_path,
        select_load_job(config, "material-value-set"),
        workbook_path,
    )

    assert result.requests == ()
    assert [issue.code for issue in result.issues] == ["value_set_not_found"]
    assert result.issues[0].sample == ("Jerseys", "Midlayers")


def test_load_value_set_fails_ambiguous_private_xlsx_values(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "materials.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Fabric Type"],
        rows=[["MAT-001", "jersey"]],
    )
    _write_value_set_workbook(
        home / "load" / "value-sets" / "materials.fabric_type.xlsx",
        ["Jersey", "Jerseys"],
    )
    config = _value_set_config(tmp_path)

    with pytest.raises(ConfigError, match="ambiguous loose values"):
        materialize_load(
            db_path,
            select_load_job(config, "material-value-set"),
            workbook_path,
        )


def test_load_reference_resolution_requires_cached_resolve_endpoints(
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
        rows=[["MAT-001", "Fabric"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "OTHER"},
        )

    config = load_load_config()

    with pytest.raises(ConfigError, match="material_types"):
        materialize_load(
            db_path,
            select_load_job(config, "material-create"),
            workbook_path,
        )


def test_material_composition_create_resolves_code_and_parses_compositions(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material", "Composition"],
        rows=[["MAT-001", "95%cotton;  5% polyester   ."]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Cotton", "active": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C2",
            payload={"id": "C2", "node_name": "Polyester", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.issues == ()
    assert result.requests[0].path == "/v2/materials/M1/technical_compositions"
    assert result.requests[0].body == [
        {"percentage": 95, "composition": "C1"},
        {"percentage": 5, "composition": "C2"},
    ]


def test_material_composition_create_accepts_direct_material_id_and_name_first(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material ID", "Fiber Content"],
        rows=[["M1", "Polyester 50%, Cotton 50%"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Cotton", "active": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C2",
            payload={"id": "C2", "node_name": "Polyester", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.issues == ()
    assert result.requests[0].path == "/v2/materials/M1/technical_compositions"
    assert result.requests[0].body == [
        {"percentage": 50, "composition": "C2"},
        {"percentage": 50, "composition": "C1"},
    ]


def test_material_composition_create_accepts_unseparated_percent_first_entries(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Content"],
        rows=[["MAT-001", "95 cotton 5 polyester"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Cotton", "active": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C2",
            payload={"id": "C2", "node_name": "Polyester", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.issues == ()
    assert result.requests[0].body == [
        {"percentage": 95, "composition": "C1"},
        {"percentage": 5, "composition": "C2"},
    ]


def test_material_composition_create_accepts_flexible_recycled_polyester_names(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Code", "Content"],
        rows=[
            ["MAT-001", "polyester-recycled 95 5 elastane"],
            ["MAT-002", "95% recycled polyester, 5% elastane"],
            ["MAT-003", "Polyester / Recycled 95; Elastane 5"],
            ["MAT-004", "95 polyester-recycled; elastane 5"],
        ],
    )
    with connect(db_path) as conn:
        for record_id, code in (
            ("M1", "MAT-001"),
            ("M2", "MAT-002"),
            ("M3", "MAT-003"),
            ("M4", "MAT-004"),
        ):
            _insert_record(
                conn,
                endpoint="materials",
                record_id=record_id,
                payload={"id": record_id, "code": code},
            )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Polyester - Recycled", "active": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C2",
            payload={"id": "C2", "node_name": "Elastane", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.issues == ()
    assert [request.body for request in result.requests] == [
        [
            {"percentage": 95, "composition": "C1"},
            {"percentage": 5, "composition": "C2"},
        ],
        [
            {"percentage": 95, "composition": "C1"},
            {"percentage": 5, "composition": "C2"},
        ],
        [
            {"percentage": 95, "composition": "C1"},
            {"percentage": 5, "composition": "C2"},
        ],
        [
            {"percentage": 95, "composition": "C1"},
            {"percentage": 5, "composition": "C2"},
        ],
    ]


def test_material_composition_create_fails_ambiguous_canonical_composition(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material", "Composition"],
        rows=[["MAT-001", "100 recycled-polyester"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Polyester - Recycled", "active": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C2",
            payload={"id": "C2", "node_name": "Recycled Polyester", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.requests == ()
    assert [issue.code for issue in result.issues] == ["composition_ambiguous"]


def test_material_composition_create_fails_duplicate_material_code(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material", "Composition"],
        rows=[["MAT-001", "100% cotton"]],
    )
    with connect(db_path) as conn:
        for record_id in ("M1", "M2"):
            _insert_record(
                conn,
                endpoint="materials",
                record_id=record_id,
                payload={"id": record_id, "code": "MAT-001"},
            )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Cotton", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.requests == ()
    assert [issue.code for issue in result.issues] == ["ref_ambiguous"]


def test_material_composition_create_fails_total_not_100(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material", "Composition"],
        rows=[["MAT-001", "90% cotton, 5% polyester"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Cotton", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.requests == ()
    assert [issue.code for issue in result.issues] == ["composition_total_invalid"]
    assert "got 95" in result.issues[0].message


def test_material_composition_create_fails_unknown_composition(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    workbook_path = tmp_path / "compositions.xlsx"
    _write_material_workbook(
        workbook_path,
        headers=["Material", "Composition"],
        rows=[["MAT-001", "100% cottn"]],
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="C1",
            payload={"id": "C1", "node_name": "Cotton", "active": True},
        )

    config = load_load_config()
    result = materialize_load(
        db_path,
        select_load_job(config, "material-composition-create"),
        workbook_path,
    )

    assert result.requests == ()
    assert [issue.code for issue in result.issues] == ["composition_not_found"]


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
        headers=["Code", "Product Type", "Material Type"],
        rows=[["MAT-001", "Fabric", "Fabric"]],
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
    assert result.issues[0].column == "product_type"
