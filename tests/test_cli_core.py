from __future__ import annotations

import json

from centric_api.cli import main
from centric_api.rendering.help import should_color, top_level_help


def test_cli_help_commands(capsys) -> None:
    assert main(["--help"]) == 0

    output = capsys.readouterr().out
    assert "fetch" in output
    assert "changelog" in output
    assert "cron" in output
    assert "download" in output
    assert "bundle" in output
    assert "view" in output
    assert "validate" in output
    assert "model" in output
    assert "units" in output
    assert "swagger" in output
    assert "CENTRIC API" in output
    assert "Recommended path:" in output
    assert "Core workflows:" in output
    assert "System & advanced:" in output
    assert "Good first commands:" in output
    assert "1. doctor" in output
    assert "centric-api load check material-create materials.xlsx" in output
    assert "centric-api validate list" in output


def test_top_level_help_can_render_color() -> None:
    output = top_level_help(color=True)

    assert "\033[" in output
    assert "\033[1m\033[36mCENTRIC API\033[0m" in output
    assert "\033[1mUsage:\033[0m" in output
    assert "\033[35mcentric-api\033[0m <command> [options]" in output
    assert "\033[1mRecommended path:\033[0m" in output
    assert "\033[32mfetch      \033[0m" in output
    assert "\033[2m1.\033[0m \033[32mdoctor   \033[0m" in output
    assert "\033[35mcentric-api\033[0m \033[32mfetch\033[0m" in output
    assert "\033[36m--endpoint\033[0m \033[2mstyles\033[0m" in output
    assert "\033[32mload\033[0m \033[32mcheck\033[0m" in output
    assert "\033[32mvalidate\033[0m \033[32mlist\033[0m" in output
    assert "\033[2mstyle-colorways-demo\033[0m" in output
    assert "\033[2mmaterial-create\033[0m" in output
    assert "\033[33mmaterials.xlsx\033[0m" in output


def test_top_level_help_color_detection(monkeypatch) -> None:
    class Stream:
        def isatty(self) -> bool:
            return False

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert not should_color(Stream())

    monkeypatch.setenv("FORCE_COLOR", "1")
    assert should_color(Stream())


def test_units_cli_convert_and_normalize(capsys) -> None:
    assert main(["units", "convert", "1500", "g", "kg"]) == 0
    output = capsys.readouterr().out
    assert "1500 g = 1.5 kg (mass)" in output

    assert main(["units", "normalize", "sq m", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"input": "sq m", "unit": "m2", "dimension": "area"}

    assert main(["units", "basis", "gsm"]) == 0
    output = capsys.readouterr().out
    assert "Basis:          areal_density" in output
    assert "BOM unit:       m" in output
    assert "Width unit:     m" in output
    assert "Requires:       bom_quantity, material_uom, material_weight, cuttable_width" in output

    assert main(["units", "basis", "pcs", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["basis"] == "per_piece_mass"
    assert payload["material_value_unit"] == "g"


def test_units_cli_uses_explicit_config(tmp_path, capsys) -> None:
    config = tmp_path / "units.yml"
    config.write_text(
        """
version: 1
dimensions:
  volume:
    base: l
    units:
      ml:
        factor: 0.001
        aliases: [milliliter]
      l:
        factor: 1
        aliases: [liter]
""",
        encoding="utf-8",
    )

    assert main(["units", "--units-config", str(config), "convert", "500", "ml", "l"]) == 0

    output = capsys.readouterr().out
    assert "500 ml = 0.5 l (volume)" in output

    assert main(["units", "list", "--units-config", str(config), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dimensions"][0]["dimension"] == "volume"


def test_grouped_command_config_flags_work_after_action(tmp_path, capsys) -> None:
    load_config = tmp_path / "load.yml"
    load_config.write_text(
        """
version: 1
jobs:
  - name: explicit-job
    method: POST
    path: /v2/explicit
    columns:
      code:
        header: Code
        required: true
    body:
      code: code
""",
        encoding="utf-8",
    )

    models_dir = tmp_path / "models"
    models_dir.mkdir()

    assert main(["load", "list", "--load-config", str(load_config), "--json"]) == 0
    load_rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["name"] for row in load_rows] == ["explicit-job"]

    assert main(["model", "list", "--models-dir", str(models_dir), "--json"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_keyboard_interrupt_returns_clean_130(monkeypatch, capsys) -> None:
    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("centric_api.cli.run_status", interrupt)

    assert main(["status"]) == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Interrupted.\n"
