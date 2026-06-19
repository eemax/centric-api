from __future__ import annotations

import json
import re

from centric_api.cli import main
from centric_api.endpoint_map import infer_endpoint_relationships
from centric_api.store import connect
from tests.helpers_load import _insert_record


def test_endpoint_map_infers_scalar_and_array_references() -> None:
    relationships = infer_endpoint_relationships(
        {
            "styles": (
                {
                    "id": "S1",
                    "season": "SE1",
                    "default_supplier": "SUP1",
                },
            ),
            "seasons": ({"id": "SE1"},),
            "suppliers": ({"id": "SUP1"},),
            "bom_lines": (
                {
                    "id": "BL1",
                    "style": "S1",
                    "only_for_colors": ["CW1", "CW2"],
                },
            ),
            "colorways": ({"id": "CW1"}, {"id": "CW2"}),
        }
    )

    assert {
        (item.source_endpoint, item.source_path, item.target_endpoint, item.array)
        for item in relationships
    } == {
        ("bom_lines", "only_for_colors[]", "colorways", True),
        ("bom_lines", "style", "styles", False),
        ("styles", "default_supplier", "suppliers", False),
        ("styles", "season", "seasons", False),
    }


def test_endpoint_map_collapses_numeric_array_like_path_segments() -> None:
    relationships = infer_endpoint_relationships(
        {
            "styles": (
                {
                    "id": "S1",
                    "parts": {
                        "0": {"material": "M1"},
                        "1": {"material": "M2"},
                        "40": {"material": "M1"},
                    },
                },
            ),
            "materials": ({"id": "M1"}, {"id": "M2"}),
        }
    )

    assert {
        (item.source_endpoint, item.source_path, item.target_endpoint, item.array)
        for item in relationships
    } == {("styles", "parts[].material", "materials", True)}


def test_map_endpoints_cli_writes_json_markdown_and_html(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "season": "SE1"},
        )
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SE1",
            payload={"id": "SE1", "node_name": "SS26"},
        )
        _insert_record(
            conn,
            endpoint="users",
            record_id="U1",
            payload={"id": "U1", "node_name": "Monica"},
        )

    assert main(["map", "endpoints", "--db", str(db_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["endpoint_count"] == 3
    assert payload["relationship_count"] == 1
    assert re.fullmatch(r"endpoint-map-\d{4}-\d{2}-\d{2}-\d{4}(?:-\d+)?", payload["run_id"])

    json_path = home / "maps" / "endpoints" / payload["run_id"] / "relationships.json"
    markdown_path = home / "maps" / "endpoints" / payload["run_id"] / "endpoint-map.md"
    html_path = home / "maps" / "endpoints" / payload["run_id"] / "endpoint-map.html"

    assert json_path.is_file()
    assert markdown_path.is_file()
    assert html_path.is_file()
    relationships_payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert relationships_payload["relationships"][0]["join"] == "styles.season = seasons.id"
    assert relationships_payload["relationships"][0]["target_path"] == "id"
    assert relationships_payload["by_endpoint"]["styles"]["outgoing"][0]["target_endpoint"] == (
        "seasons"
    )
    assert relationships_payload["by_endpoint"]["seasons"]["incoming"][0]["source_endpoint"] == (
        "styles"
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## How To Read" in markdown
    assert "## High-Level Graph" in markdown
    assert "## Join Recipes" in markdown
    assert "`season` -> `seasons`" in markdown
    assert "`styles.season = seasons.id`" in markdown
    assert "### users" in markdown
    assert "- none detected" in markdown
    html = html_path.read_text(encoding="utf-8")
    assert "Endpoint Relationship Map" in html
    assert "__ENDPOINTS_JSON__" not in html
    assert '"styles"' in html
