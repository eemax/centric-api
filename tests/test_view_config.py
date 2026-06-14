from __future__ import annotations

from pathlib import Path

import pytest

from centric_api.config import ConfigError
from centric_api.view_config import load_view_config, select_view


def test_default_view_config_loads_style_colorways_demo() -> None:
    config = load_view_config(Path("config/views.yml"))

    view = select_view(config, "style-colorways-demo")

    assert view.root.endpoint == "styles"
    assert view.columns


def test_view_rejects_independent_expansion_chains(tmp_path: Path) -> None:
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: invalid
    root:
      endpoint: styles
      as: style
    joins:
      - as: colorway
        endpoint: colorways
        from: style.id
        to: style
        relationship: many_expand
      - as: document
        endpoint: documents
        from: style.documents
        to: id
        relationship: many_expand
    columns:
      - header: Style
        path: style.node_name
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="multiple independent many_expand"):
        load_view_config(config_path)


def test_view_allows_one_join_inside_expansion_chain(tmp_path: Path) -> None:
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: nested
    root:
      endpoint: styles
      as: style
    joins:
      - as: colorway
        endpoint: colorways
        from: style.id
        to: style
        relationship: many_expand
      - as: bom
        endpoint: boms
        from: colorway.bom
        to: id
        relationship: one
      - as: bom_line
        endpoint: bom_lines
        from: bom.id
        to: bom
        relationship: many_expand
    columns:
      - header: Style
        path: style.node_name
      - header: BOM Line
        path: bom_line.node_name
""",
        encoding="utf-8",
    )

    config = load_view_config(config_path)

    assert select_view(config, "nested").joins[-1].alias == "bom_line"


def test_view_config_rejects_invalid_regex_filter(tmp_path: Path) -> None:
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: invalid-regex
    root:
      endpoint: styles
      as: style
    filters:
      - path: style.node_name
        matches: "["
    columns:
      - header: Style
        path: style.node_name
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="valid regex"):
        load_view_config(config_path)
