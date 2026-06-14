from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from tests.helpers_view import _seed_bom_line_view_records, _view_config


def test_view_cli_list_show_and_export_json(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "centric.db"
    _seed_bom_line_view_records(db_path)
    config_path = _view_config(tmp_path)
    output_path = tmp_path / "export.csv"

    assert main(["view", "list", "--view-config", str(config_path)]) == 0
    list_output = capsys.readouterr().out
    assert "Configured Views" in list_output
    assert "bom-lines" in list_output

    assert main(["view", "show", "bom-lines", "--view-config", str(config_path)]) == 0
    show_output = capsys.readouterr().out
    assert "View: bom-lines" in show_output
    assert "many_expand" not in show_output

    assert (
        main(
            [
                "view",
                "check",
                "bom-lines",
                "--view-config",
                str(config_path),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["view"] == "bom-lines"
    assert check_payload["ok"] is True
    assert check_payload["rows_scanned"] == 2
    assert check_payload["rows_projected"] == 2
    assert check_payload["missing_join_details"] == []

    assert (
        main(
            [
                "view",
                "export",
                "bom-lines",
                "--view-config",
                str(config_path),
                "--db",
                str(db_path),
                "--output",
                str(output_path),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["view"] == "bom-lines"
    assert payload["format"] == "csv"
    assert payload["rows"] == 2
    assert payload["missing_join_details"] == []
    assert output_path.is_file()
