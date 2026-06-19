from __future__ import annotations

import json
from pathlib import Path

import pytest

from centric_api.cli import main
from centric_api.swagger.history import history_diff_snapshots
from tests.helpers_swagger import (
    _fake_auth_context,
    _swagger_doc,
    _write_history_snapshot,
    _write_home_swagger,
)


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
