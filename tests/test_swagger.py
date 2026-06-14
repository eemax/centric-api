from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from centric_api.cli import main
from centric_api.swagger import build_swagger_index


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
        },
        {
            "endpoint": "styles",
            "method": "GET",
            "operation_id": "listStyles",
            "path": "/v2/styles",
        },
        {
            "endpoint": "styles",
            "method": "POST",
            "operation_id": "createStyle",
            "path": "/v2/styles",
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
    assert payload["added_count"] == 1
    assert payload["added"] == [{"endpoint": "materials", "method": "GET", "path": "/v2/materials"}]


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
    assert payload["missing_in_swagger"] == [{"name": "boms", "path": "/v2/boms"}]
    assert payload["missing_in_config"] == [{"endpoint": "materials", "path": "/v2/materials"}]


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
    (home / "swagger.json").parent.mkdir(parents=True)
    (home / "swagger.json").write_text(json.dumps(old_document), encoding="utf-8")
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
    meta = json.loads((home / "swagger.meta.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert json.loads((home / "swagger.json").read_text(encoding="utf-8")) == new_document
    assert payload["last_diff"]["added_count"] == 1
    assert meta["last_diff"]["added"] == [
        {"endpoint": "materials", "method": "GET", "path": "/v2/materials"}
    ]
    assert meta["url"] == (
        "https://brand.centricsoftware.com/csi-requesthandler/api/v2/swagger.json"
    )


def test_swagger_status_reports_unreadable_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    (home / "swagger.json").write_text(json.dumps(_swagger_doc()), encoding="utf-8")
    (home / "swagger.meta.json").write_text("{", encoding="utf-8")

    exit_code = main(["swagger", "status", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["swagger_exists"] is True
    assert payload["meta_exists"] is True
    assert payload["meta"] is None
    assert payload["meta_error"].startswith("Swagger metadata is not valid JSON:")
    assert payload["operation_count"] == 3


def test_swagger_index_resolves_body_schema_fields() -> None:
    index = build_swagger_index(_swagger_doc())
    create_style = next(
        operation
        for operation in index.operations
        if operation.method == "POST" and operation.path == "/v2/styles"
    )

    assert create_style.body_fields == ("code", "node_name")
    assert create_style.required_body_fields == ("code",)


def _swagger_doc(paths: list[str] | None = None) -> dict[str, Any]:
    selected_paths = paths or ["/v2/color_specifications", "/v2/styles"]
    payload: dict[str, Any] = {
        "swagger": "2.0",
        "paths": {},
        "definitions": {
            "StyleCreate": {
                "type": "object",
                "required": ["code"],
                "properties": {
                    "code": {"type": "string"},
                    "node_name": {"type": "string"},
                },
            }
        },
    }
    for path in selected_paths:
        endpoint = path.rsplit("/", 1)[-1]
        payload["paths"][path] = {
            "get": {
                "operationId": f"list{endpoint.title().replace('_', '')}",
                "parameters": [{"name": "decoded", "in": "query"}],
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
        }
    return payload


def _write_home_swagger(tmp_path: Path, monkeypatch, document: dict[str, Any]) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    swagger_path = home / "swagger.json"
    swagger_path.write_text(json.dumps(document), encoding="utf-8")
    return swagger_path


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
