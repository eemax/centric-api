from __future__ import annotations

import csv
from pathlib import Path

import pytest

import centric_api.view_export as view_export_module
from centric_api.store import connect
from centric_api.view_config import load_view_config, select_view
from centric_api.view_export import export_view
from tests.helpers_view import _insert_record


def test_view_many_concat_preserves_row_grain_and_csv_output(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={
                "id": "S1",
                "node_name": "Linen Shirt",
                "active": True,
                "documents": ["D1", "D2", "D3"],
            },
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S2",
            payload={
                "id": "S2",
                "node_name": "Inactive Style",
                "active": False,
                "documents": ["D1"],
            },
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D1",
            payload={"id": "D1", "node_name": "Spec.pdf", "active": True},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D2",
            payload={"id": "D2", "node_name": "Artwork.ai", "active": True},
        )
        _insert_record(
            conn,
            endpoint="documents",
            record_id="D3",
            payload={"id": "D3", "node_name": "Old.pdf", "active": False},
        )

    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
output_dir: exports
views:
  - name: style-docs
    title: Style Documents
    root:
      endpoint: styles
      as: style
    joins:
      - as: document
        endpoint: documents
        from: style.documents
        to: id
        relationship: many_concat
        separator: " | "
        filters:
          - path: document.active
            equals: true
          - path: document.node_name
            matches: '\\.pdf$'
    filters:
      - path: style.active
        equals: true
      - path: document.id
        exists: true
    columns:
      - header: Style
        path: style.node_name
      - header: Documents
        path: document.node_name
""",
        encoding="utf-8",
    )
    config = load_view_config(config_path)
    view = select_view(config, "style-docs")

    result = export_view(
        db_path,
        config,
        view,
        export_format="csv",
        output_path=tmp_path / "style-docs.csv",
    )

    assert result.row_count == 1
    with result.output_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [["Style", "Documents"], ["Linen Shirt", "Spec.pdf"]]


def test_view_export_escapes_formula_like_csv_text(tmp_path: Path) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "=SUM(1,1)", "quantity": "-5"},
        )
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: formula-csv
    root:
      endpoint: styles
      as: style
    columns:
      - header: Name
        path: style.node_name
      - header: Quantity
        path: style.quantity
        type: number
""",
        encoding="utf-8",
    )
    config = load_view_config(config_path)
    result = export_view(
        db_path,
        config,
        select_view(config, "formula-csv"),
        export_format="csv",
        output_path=tmp_path / "formula-text.csv",
    )

    with result.output_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))

    assert rows == [["Name", "Quantity"], ["'=SUM(1,1)", "-5"]]


def test_view_export_streams_simple_table_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
        conn.execute("CREATE TABLE model_export (code TEXT, name TEXT, active INTEGER)")
        conn.executemany(
            "INSERT INTO model_export VALUES (?, ?, ?)",
            [("S1", "One", 1), ("S2", "Two", 0)],
        )
    config_path = tmp_path / "views.yml"
    config_path.write_text(
        """
version: 1
views:
  - name: streamed-model
    root:
      table: model_export
      as: model
    filters:
      - path: model.active
        equals: 1
    columns:
      - header: Code
        path: model.code
      - header: Name
        path: model.name
""",
        encoding="utf-8",
    )

    def fail_materialize(*_args, **_kwargs):
        raise AssertionError("materialized")

    monkeypatch.setattr(view_export_module, "materialize_view", fail_materialize)
    config = load_view_config(config_path)

    result = export_view(
        db_path,
        config,
        select_view(config, "streamed-model"),
        export_format="csv",
        output_path=tmp_path / "streamed-model.csv",
    )

    assert result.row_count == 1
    with result.output_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [["Code", "Name"], ["S1", "One"]]


def test_view_csv_export_preserves_existing_file_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "centric.db"
    with connect(db_path) as conn:
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
  - name: styles
    root:
      endpoint: styles
      as: style
    columns:
      - header: Style
        path: style.node_name
""",
        encoding="utf-8",
    )
    output_path = tmp_path / "styles.csv"
    output_path.write_text("existing\n", encoding="utf-8")

    class FailingWriter:
        def writerow(self, _row) -> None:
            raise RuntimeError("csv write failed")

    monkeypatch.setattr(view_export_module.csv, "writer", lambda _fh: FailingWriter())

    config = load_view_config(config_path)
    with pytest.raises(RuntimeError, match="csv write failed"):
        export_view(
            db_path,
            config,
            select_view(config, "styles"),
            export_format="csv",
            output_path=output_path,
        )

    assert output_path.read_text(encoding="utf-8") == "existing\n"
    assert not (tmp_path / ".styles.csv.tmp").exists()
