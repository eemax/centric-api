from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from centric_api.cli import main
from centric_api.swagger import build_swagger_index
from centric_api.swagger.history import history_diff_snapshots


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


def test_swagger_diff_compares_against_previous_schema(tmp_path: Path, monkeypatch, capsys) -> None:
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(_swagger_doc(paths=["/v2/styles"])), encoding="utf-8")
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc(paths=["/v2/styles", "/v2/materials"]))

    exit_code = main(
        [
            "swagger",
            "diff",
            "--against",
            str(previous_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["operation_added_count"] == 1
    assert payload["operations"]["added"] == [
        {"endpoint": "materials", "method": "GET", "path": "/v2/materials"}
    ]


def test_swagger_diff_reports_field_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(
        json.dumps(_swagger_doc(style_create_properties=["code"])),
        encoding="utf-8",
    )
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc())

    exit_code = main(
        [
            "swagger",
            "diff",
            "--against",
            str(previous_path),
            "--endpoint",
            "styles",
            "--method",
            "post",
            "--fields-only",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    group = payload["fields"]["groups"][0]
    assert exit_code == 1
    assert payload["field_added_count"] == 1
    assert payload["operation_added_count"] == 0
    assert group["endpoint"] == "styles"
    assert group["method"] == "POST"
    assert group["surface"] == "request"
    assert group["added"][0]["name"] == "node_name"
    assert group["added"][0]["type"] == "string"


def test_swagger_diff_reports_field_attribute_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    previous = _swagger_doc()
    current = _swagger_doc()
    previous_path = tmp_path / "previous.json"
    previous["definitions"]["StyleCreate"]["properties"]["code"]["enum"] = ["OLD001"]
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    current["definitions"]["StyleCreate"]["properties"]["code"].update(
        {"enum": ["ACC001"], "format": "centric-code"}
    )
    _write_home_swagger(tmp_path, monkeypatch, current)

    exit_code = main(
        [
            "swagger",
            "diff",
            "--against",
            str(previous_path),
            "--endpoint",
            "styles",
            "--method",
            "post",
            "--fields-only",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    changed = payload["fields"]["groups"][0]["changed"][0]
    assert exit_code == 1
    assert changed["name"] == "code"
    assert changed["changes"]["format"] == {"from": None, "to": "centric-code"}
    assert changed["changes"]["enum"] == {
        "added": ["ACC001"],
        "removed": ["OLD001"],
        "added_count": 1,
        "removed_count": 1,
    }


def test_swagger_diff_human_output_does_not_truncate_long_field_values(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    previous = _swagger_doc()
    current = _swagger_doc()
    previous_path = tmp_path / "previous.json"
    previous["definitions"]["StyleCreate"]["properties"]["code"]["enum"] = ["OLD001"]
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    current["definitions"]["StyleCreate"]["properties"]["code"]["enum"] = [
        "DemoBrandCategory:" + ", ".join(f"Value {index:02d}" for index in range(50))
    ]
    _write_home_swagger(tmp_path, monkeypatch, current)

    exit_code = main(
        [
            "swagger",
            "diff",
            "--against",
            str(previous_path),
            "--endpoint",
            "styles",
            "--method",
            "post",
            "--fields-only",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "code enum +1 -1" in output
    assert "+ DemoBrandCategory:" in output
    assert "- OLD001" in output
    assert "Value 49" in output
    assert "..." not in output


def test_swagger_coverage_compares_fetch_config(tmp_path: Path, monkeypatch, capsys) -> None:
    fetch_config = tmp_path / "fetcher.yml"
    _write_home_swagger(
        tmp_path,
        monkeypatch,
        _swagger_doc(paths=["/v2/styles", "/v2/styles/count", "/v2/materials"]),
    )
    fetch_config.write_text(
        """
timeout: 10
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: styles/count
  - name: boms
    api_version: v2
    path: boms
    count_spec:
      path: boms/count
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "swagger",
            "coverage",
            "--fetch-config",
            str(fetch_config),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["covered_count"] == 1
    assert payload["swagger_only_post_count"] == 0
    assert payload["covered"][0]["endpoint"] == "styles"
    assert payload["covered"][0]["configured_path"] == "/v2/styles"
    assert payload["covered"][0]["swagger_path"] == "/v2/styles"
    assert payload["covered"][0]["response_field_count"] == 2
    assert payload["covered"][0]["has_post"] is True
    assert payload["covered"][0]["post_field_count"] == 2
    assert payload["missing_in_swagger"] == [{"name": "boms", "configured_path": "/v2/boms"}]
    assert payload["missing_in_config"] == [
        {
            "endpoint": "materials",
            "swagger_path": "/v2/materials",
            "response_schema": "materials information",
            "response_field_count": 2,
            "required_response_field_count": 1,
            "has_post": False,
            "post_schema": None,
            "post_field_count": 0,
            "required_post_field_count": 0,
        }
    ]


def test_swagger_coverage_accepts_unversioned_swagger_paths(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    fetch_config = tmp_path / "fetcher.yml"
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc(paths=["/styles"]))
    fetch_config.write_text(
        """
timeout: 10
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: styles/count
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "swagger",
            "coverage",
            "--fetch-config",
            str(fetch_config),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["missing_in_swagger"] == []
    assert payload["missing_in_config"] == []


def test_swagger_refresh_writes_home_files_and_metadata(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    old_document = _swagger_doc(paths=["/v2/styles"])
    new_document = _swagger_doc(paths=["/v2/styles", "/v2/materials"])
    current_path = home / "swagger" / "current.json"
    current_meta_path = home / "swagger" / "current.meta.json"
    current_path.parent.mkdir(parents=True)
    current_path.write_text(json.dumps(old_document), encoding="utf-8")
    fetch_config = tmp_path / "fetcher.yml"
    fetch_config.write_text(
        """
timeout: 10
endpoints:
  - name: styles
    api_version: v2
    path: styles
    count_spec:
      path: styles/count
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    monkeypatch.setenv("CENTRIC_BASE_URL", "brand")
    monkeypatch.setenv("CENTRIC_USERNAME", "user")
    monkeypatch.setenv("CENTRIC_PASSWORD", "pass")
    monkeypatch.setattr(
        "centric_api.commands.swagger.AuthContext",
        _fake_auth_context(new_document),
    )

    exit_code = main(["swagger", "refresh", "--fetch-config", str(fetch_config), "--json"])

    payload = json.loads(capsys.readouterr().out)
    meta = json.loads(current_meta_path.read_text(encoding="utf-8"))
    history_path = Path(payload["history_path"])
    history_meta_path = Path(payload["history_meta_path"])
    assert exit_code == 0
    assert json.loads(current_path.read_text(encoding="utf-8")) == new_document
    assert json.loads(history_path.read_text(encoding="utf-8")) == new_document
    assert history_path.parent == home / "swagger" / "history"
    assert history_meta_path.parent == home / "swagger" / "history"
    assert history_path.name == f"{payload['snapshot_id']}.json"
    assert history_meta_path.name == f"{payload['snapshot_id']}.meta.json"
    assert payload["last_diff"]["operation_added_count"] == 1
    assert payload["last_diff"]["field_added_count"] == 2
    assert meta["last_diff"]["operations"]["added"] == [
        {"endpoint": "materials", "method": "GET", "path": "/v2/materials"}
    ]
    assert json.loads(history_meta_path.read_text(encoding="utf-8"))["path"] == str(history_path)
    assert meta["path"] == str(current_path)
    assert meta["history_path"] == str(history_path)
    assert meta["field_schema_count"] == 3
    assert meta["url"] == (
        "https://brand.centricsoftware.com/csi-requesthandler/api/v2/swagger.json"
    )


def test_swagger_history_lists_snapshots_newest_first(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    _write_history_snapshot(
        home,
        "20260614T100000000000",
        _swagger_doc(paths=["/v2/styles"]),
        fetched_at="2026-06-14T10:00:00.000000Z",
    )
    _write_history_snapshot(
        home,
        "20260614T110000000000",
        _swagger_doc(paths=["/v2/styles", "/v2/materials"]),
        fetched_at="2026-06-14T11:00:00.000000Z",
    )

    exit_code = main(["swagger", "history", "--json"])

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert [row["index"] for row in rows] == [0, 1]
    assert [row["snapshot_id"] for row in rows] == [
        "20260614T110000000000",
        "20260614T100000000000",
    ]
    assert rows[0]["operation_count"] == 3
    assert rows[1]["operation_count"] == 2


def test_swagger_history_diffs_lists_adjacent_diff_counts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    _write_history_snapshot(
        home,
        "20260614T100000000000",
        _swagger_doc(paths=["/v2/styles"]),
        fetched_at="2026-06-14T10:00:00.000000Z",
    )
    _write_history_snapshot(
        home,
        "20260614T110000000000",
        _swagger_doc(paths=["/v2/styles", "/v2/materials"]),
        fetched_at="2026-06-14T11:00:00.000000Z",
    )
    _write_history_snapshot(
        home,
        "20260614T120000000000",
        _swagger_doc(paths=["/v2/styles", "/v2/materials", "/v2/suppliers"]),
        fetched_at="2026-06-14T12:00:00.000000Z",
    )

    exit_code = main(["swagger", "history", "--diffs", "--json"])

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert [
        (row["current_index"], row["baseline_index"], row["operation_added_count"])
        for row in rows
    ] == [(0, 1, 1), (1, 2, 1)]
    assert [row["field_added_count"] for row in rows] == [2, 2]


def test_swagger_diff_compares_history_positions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    _write_history_snapshot(
        home,
        "20260614T100000000000",
        _swagger_doc(paths=["/v2/styles"]),
        fetched_at="2026-06-14T10:00:00.000000Z",
    )
    _write_history_snapshot(
        home,
        "20260614T110000000000",
        _swagger_doc(paths=["/v2/styles", "/v2/materials"]),
        fetched_at="2026-06-14T11:00:00.000000Z",
    )

    exit_code = main(["swagger", "diff", "--history", "0", "1", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["comparison"] == {
        "source": "history",
        "current_index": 0,
        "current_snapshot_id": "20260614T110000000000",
        "current_path": str(home / "swagger" / "history" / "20260614T110000000000.json"),
        "baseline_index": 1,
        "baseline_snapshot_id": "20260614T100000000000",
        "baseline_path": str(home / "swagger" / "history" / "20260614T100000000000.json"),
    }
    assert payload["operations"]["added"] == [
        {"endpoint": "materials", "method": "GET", "path": "/v2/materials"}
    ]
    assert payload["field_added_count"] == 2


def test_swagger_diff_rejects_negative_history_indexes(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    _write_history_snapshot(
        home,
        "20260614T100000000000",
        _swagger_doc(paths=["/v2/styles"]),
        fetched_at="2026-06-14T10:00:00.000000Z",
    )
    _write_history_snapshot(
        home,
        "20260614T110000000000",
        _swagger_doc(paths=["/v2/styles", "/v2/materials"]),
        fetched_at="2026-06-14T11:00:00.000000Z",
    )

    with pytest.raises(ValueError, match="Available indexes: 0..1"):
        history_diff_snapshots([-1, 0])


def test_swagger_status_reports_unreadable_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    current_path = home / "swagger" / "current.json"
    current_meta_path = home / "swagger" / "current.meta.json"
    current_path.parent.mkdir(parents=True)
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    current_path.write_text(json.dumps(_swagger_doc()), encoding="utf-8")
    current_meta_path.write_text("{", encoding="utf-8")

    exit_code = main(["swagger", "status", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["swagger_exists"] is True
    assert payload["meta_exists"] is True
    assert payload["meta"] is None
    assert payload["meta_error"].startswith("Swagger metadata is not valid JSON:")
    assert payload["operation_count"] == 3


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


def test_swagger_coverage_human_output_does_not_truncate_swagger_only_rows(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    paths = [f"/v2/generated_{index:02d}" for index in range(45)]
    fetch_config = tmp_path / "fetcher.yml"
    fetch_config.write_text(
        """
timeout: 10
endpoints:
  - name: configured_only
    api_version: v2
    path: configured_only
    count_spec:
      path: configured_only/count
""",
        encoding="utf-8",
    )
    _write_home_swagger(tmp_path, monkeypatch, _swagger_doc(paths=paths))

    exit_code = main(["swagger", "coverage", "--fetch-config", str(fetch_config)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Path" in output
    assert "Get fields" in output
    assert "generated_44" in output
    assert "... " not in output


def test_swagger_index_resolves_body_schema_fields() -> None:
    index = build_swagger_index(_swagger_doc())
    create_style = next(
        operation
        for operation in index.operations
        if operation.method == "POST" and operation.path == "/v2/styles"
    )

    assert create_style.body_fields == ("code", "node_name")
    assert create_style.required_body_fields == ("code",)


def _swagger_doc(
    paths: list[str] | None = None,
    *,
    style_create_properties: list[str] | None = None,
) -> dict[str, Any]:
    selected_paths = paths or ["/v2/color_specifications", "/v2/styles"]
    style_create_properties = style_create_properties or ["code", "node_name"]
    payload: dict[str, Any] = {
        "swagger": "2.0",
        "paths": {},
        "definitions": {
            "StyleCreate": {
                "type": "object",
                "required": ["code"],
                "properties": {
                    key: value
                    for key, value in {
                        "code": {"type": "string", "description": "The style code."},
                        "node_name": {
                            "type": "string",
                            "description": "The display name of the Style.",
                        },
                    }.items()
                    if key in style_create_properties
                },
            }
        },
    }
    for path in selected_paths:
        endpoint = path.rsplit("/", 1)[-1]
        info_schema = f"{endpoint} information"
        payload["definitions"][info_schema] = {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "The unique identifier."},
                "node_name": {"type": "string", "description": "The display name."},
            },
        }
        payload["paths"][path] = {
            "get": {
                "operationId": f"list{endpoint.title().replace('_', '')}",
                "parameters": [{"name": "decoded", "in": "query"}],
                "responses": {
                    "200": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": f"#/definitions/{info_schema}"},
                        }
                    }
                },
            }
        }
    if "/v2/styles" in selected_paths:
        payload["paths"]["/v2/styles"]["post"] = {
            "operationId": "createStyle",
            "parameters": [
                {
                    "name": "body",
                    "in": "body",
                    "schema": {"$ref": "#/definitions/StyleCreate"},
                }
            ],
            "responses": {"201": {"schema": {"$ref": "#/definitions/styles information"}}},
        }
    return payload


def _write_home_swagger(tmp_path: Path, monkeypatch, document: dict[str, Any]) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    swagger_path = home / "swagger" / "current.json"
    swagger_path.parent.mkdir(parents=True)
    swagger_path.write_text(json.dumps(document), encoding="utf-8")
    return swagger_path


def _write_history_snapshot(
    home: Path,
    snapshot_id: str,
    document: dict[str, Any],
    *,
    fetched_at: str,
) -> None:
    history_dir = home / "swagger" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{snapshot_id}.json"
    meta_path = history_dir / f"{snapshot_id}.meta.json"
    index = build_swagger_index(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "fetched_at": fetched_at,
                "path": str(path),
                "swagger_version": index.swagger_version,
                "operation_count": index.operation_count,
                "endpoint_count": len(index.endpoints),
                "field_schema_count": len(
                    {
                        schema
                        for operation in index.operations
                        for schema in (operation.request_schema, operation.response_schema)
                        if schema
                    }
                ),
                "field_count": sum(
                    len(operation.request_fields) + len(operation.response_fields)
                    for operation in index.operations
                ),
                "sha256": f"sha-{snapshot_id}",
            }
        ),
        encoding="utf-8",
    )


def _fake_auth_context(document: dict[str, Any]):
    class Response:
        status_code = 200
        text = json.dumps(document)

        def json(self) -> dict[str, Any]:
            return document

    class FakeAuthContext:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeAuthContext:
            return self

        def __exit__(self, *_args: Any) -> None:
            pass

        def request(self, method: str, url: str):
            assert method == "GET"
            assert url.endswith("/api/v2/swagger.json")
            return Response()

    return FakeAuthContext
