from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from centric_api.swagger import build_swagger_index
from tests.helpers_swagger import _swagger_doc, _write_home_swagger


def test_swagger_endpoints_lists_local_schema(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(["swagger", "endpoints", "--json"])

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert rows == [
        {
            "endpoint": "color_specifications",
            "method": "GET",
            "operation_id": "listColorSpecifications",
            "path": "/v2/color_specifications",
            "request_schema": None,
            "response_schema": "color_specifications information",
            "request_field_count": 0,
            "response_field_count": 2,
        },
        {
            "endpoint": "styles",
            "method": "GET",
            "operation_id": "listStyles",
            "path": "/v2/styles",
            "request_schema": None,
            "response_schema": "styles information",
            "request_field_count": 0,
            "response_field_count": 2,
        },
        {
            "endpoint": "styles",
            "method": "POST",
            "operation_id": "createStyle",
            "path": "/v2/styles",
            "request_schema": "StyleCreate",
            "response_schema": "styles information",
            "request_field_count": 2,
            "response_field_count": 2,
        },
    ]


def test_swagger_fields_lists_current_schema_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(["swagger", "fields", "--endpoint", "styles", "--method", "post", "--json"])

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert rows == [
        {
            "index": 5,
            "endpoint": "styles",
            "method": "POST",
            "path": "/v2/styles",
            "surface": "request",
            "schema": "StyleCreate",
            "name": "code",
            "type": "string",
            "item_type": None,
            "ref": None,
            "object_type": None,
            "format": None,
            "enum": None,
            "required": True,
            "description": "The style code.",
        },
        {
            "index": 6,
            "endpoint": "styles",
            "method": "POST",
            "path": "/v2/styles",
            "surface": "request",
            "schema": "StyleCreate",
            "name": "node_name",
            "type": "string",
            "item_type": None,
            "ref": None,
            "object_type": None,
            "format": None,
            "enum": None,
            "required": False,
            "description": "The display name of the Style.",
        },
        {
            "index": 7,
            "endpoint": "styles",
            "method": "POST",
            "path": "/v2/styles",
            "surface": "response",
            "schema": "styles information",
            "name": "id",
            "type": "string",
            "item_type": None,
            "ref": None,
            "object_type": None,
            "format": None,
            "enum": None,
            "required": True,
            "description": "The unique identifier.",
        },
        {
            "index": 8,
            "endpoint": "styles",
            "method": "POST",
            "path": "/v2/styles",
            "surface": "response",
            "schema": "styles information",
            "name": "node_name",
            "type": "string",
            "item_type": None,
            "ref": None,
            "object_type": None,
            "format": None,
            "enum": None,
            "required": False,
            "description": "The display name.",
        },
    ]


def test_swagger_fields_can_filter_to_required_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(
        [
            "swagger",
            "fields",
            "--endpoint",
            "styles",
            "--method",
            "post",
            "--required-only",
            "--json",
        ]
    )

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert [row["name"] for row in rows] == ["code", "id"]
    assert [row["index"] for row in rows] == [5, 7]


def test_swagger_field_inspects_global_index_with_full_enum(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    document = _swagger_doc()
    long_enum = "DemoBrandCategory:" + ", ".join(f"Value {index:02d}" for index in range(50))
    document["definitions"]["StyleCreate"]["properties"]["code"]["enum"] = [long_enum]
    _write_home_swagger(tmp_path, monkeypatch, document)

    exit_code = main(["swagger", "field", "5"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Index:      5" in output
    assert "Field:      code" in output
    assert "Enum:       1 values" in output
    assert "Value 49" in output
    assert "..." not in output


def test_swagger_field_inspects_endpoint_scoped_name(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(["swagger", "field", "--endpoint", "styles", "code", "--json"])

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert [row["index"] for row in rows] == [5]
    assert rows[0]["name"] == "code"


def test_swagger_endpoints_human_output_uses_table_headers(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(["swagger", "endpoints", "--endpoint", "styles"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Method  Path" in output
    assert "Req fields" in output
    assert "Resp fields" in output
    assert "Endpoint" not in output
    assert "POST    /v2/styles" in output
    assert "StyleCreate" in output


def test_swagger_fields_endpoint_human_output_uses_field_tables(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(["swagger", "fields", "--endpoint", "styles", "--method", "post"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "POST /v2/styles request (StyleCreate): 2 fields, 1 required" in output
    assert "Index  Required  Field" in output
    assert "5  yes       code" in output
    assert "node_name" in output


def test_swagger_fields_endpoint_human_output_truncates_long_cells(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    document = _swagger_doc()
    long_enum = "DemoBrandCategory:" + ", ".join(f"Value {index:02d}" for index in range(50))
    document["definitions"]["StyleCreate"]["properties"]["code"]["enum"] = [long_enum]
    _write_home_swagger(tmp_path, monkeypatch, document)

    exit_code = main(["swagger", "fields", "--endpoint", "styles", "--method", "post"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "string enum=['DemoBrandCatego..." in output
    assert "Value 49" not in output
    assert max(len(line) for line in output.splitlines()) < 190


def test_swagger_fields_endpoint_human_output_wraps_descriptions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    document = _swagger_doc()
    document["definitions"]["styles information"]["properties"]["id"]["description"] = (
        "This description is intentionally long so the human table has to wrap it onto "
        "a continuation line instead of widening the entire output."
    )
    _write_home_swagger(tmp_path, monkeypatch, document)

    exit_code = main(["swagger", "fields", "--endpoint", "styles", "--method", "get"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "This description is intentionally long so the" in output
    assert "instead of widening the entire" in output
    assert max(len(line) for line in output.splitlines()) < 190


def test_swagger_index_resolves_body_schema_fields() -> None:
    index = build_swagger_index(_swagger_doc())
    create_style = next(
        operation
        for operation in index.operations
        if operation.method == "POST" and operation.path == "/v2/styles"
    )

    assert create_style.body_fields == ("code", "node_name")
    assert create_style.required_body_fields == ("code",)
