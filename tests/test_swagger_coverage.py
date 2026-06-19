from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from tests.helpers_swagger import _swagger_doc, _write_home_swagger


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
