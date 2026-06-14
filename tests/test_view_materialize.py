from __future__ import annotations

from pathlib import Path

import pytest

from centric_api.config import ConfigError
from centric_api.store import connect
from centric_api.view_config import load_view_config, select_view
from centric_api.view_export import materialize_view
from tests.helpers_view import _insert_record, _view_config


def test_view_export_can_use_model_output_table_as_root(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE model_material_footprint (
                style_id TEXT,
                bom_id TEXT,
                composition_name TEXT,
                consumed_mass_kg REAL,
                row_status TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO model_material_footprint (
                style_id, bom_id, composition_name, consumed_mass_kg, row_status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("S1", "B1", "Cotton", 1.25, "ok"),
                ("S1", "B1", "Polyester", 0.75, "ok"),
                ("S1", "B1", None, None, "error"),
            ],
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "Linen Shirt"},
        )
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: material-footprint
    root:
      table: model_material_footprint
      as: footprint
    joins:
      - as: style
        endpoint: styles
        from: footprint.style_id
        to: id
        relationship: one
    filters:
      - path: footprint.row_status
        equals: ok
    columns:
      - header: Style
        path: style.node_name
      - header: Composition
        path: footprint.composition_name
      - header: KG
        path: footprint.consumed_mass_kg
        type: number
""",
        encoding="utf-8",
    )
    config = load_view_config(config_path)
    view = select_view(config, "material-footprint")

    materialized = materialize_view(db_path, view)

    assert view.root.table == "model_material_footprint"
    assert materialized.root_row_count == 3
    assert materialized.rows == (
        ("Linen Shirt", "Cotton", 1.25),
        ("Linen Shirt", "Polyester", 0.75),
    )


def test_view_missing_model_output_root_table_is_clear(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path):
        pass
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: missing-model
    root:
      table: model_missing
      as: model
    columns:
      - header: Style
        path: model.style_id
""",
        encoding="utf-8",
    )
    config = load_view_config(config_path)

    with pytest.raises(ConfigError, match="Run the model that creates it first"):
        materialize_view(db_path, select_view(config, "missing-model"))


def test_view_missing_join_defaults_to_blank(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        for index in range(12):
            _insert_record(
                conn,
                endpoint="bom_lines",
                record_id=f"BL{index:02d}",
                payload={"id": f"BL{index:02d}", "style": f"MISSING{index:02d}"},
            )
    config = load_view_config(_view_config(tmp_path))
    view = select_view(config, "bom-lines")

    materialized = materialize_view(db_path, view)

    assert materialized.missing_join_count == 48
    assert [
        (item.alias, item.endpoint, item.missing_count)
        for item in materialized.missing_join_details
    ] == [
        ("style", "styles", 12),
        ("colorway", "colorways", 12),
        ("season", "seasons", 12),
        ("supplier", "suppliers", 12),
    ]
    assert materialized.missing_join_details[0].missing_ref_count == 12
    assert materialized.missing_join_details[0].missing_endpoint is True
    assert materialized.missing_join_details[0].sample_keys == tuple(
        f"MISSING{index:02d}" for index in range(10)
    )
    assert materialized.missing_join_details[1].missing_source_count == 12
    assert materialized.rows[0][1:5] == (None, None, None, None)


def test_view_root_filter_runs_before_join_missing_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="bom_lines",
            record_id="BL1",
            payload={"id": "BL1", "style": "S1", "active": True},
        )
        _insert_record(
            conn,
            endpoint="bom_lines",
            record_id="BL2",
            payload={"id": "BL2", "style": "MISSING", "active": False},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "Linen Shirt"},
        )
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: filtered
    root:
      endpoint: bom_lines
      as: bom_line
    joins:
      - as: style
        endpoint: styles
        from: bom_line.style
        to: id
        relationship: one
    filters:
      - path: bom_line.active
        equals: true
    columns:
      - header: BOM Line
        path: bom_line.id
      - header: Style
        path: style.node_name
""",
        encoding="utf-8",
    )
    config = load_view_config(config_path)

    materialized = materialize_view(db_path, select_view(config, "filtered"))

    assert materialized.missing_join_count == 0
    assert materialized.rows == (("BL1", "Linen Shirt"),)
