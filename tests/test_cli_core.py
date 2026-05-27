from __future__ import annotations

import json

import pytest

from centric_api.cli import main


def test_cli_help_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "fetch" in output
    assert "changelog" in output
    assert "cron" in output
    assert "download" in output
    assert "bundle" in output
    assert "view" in output
    assert "model" in output
    assert "units" in output

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

def test_cli_keyboard_interrupt_returns_clean_130(monkeypatch, capsys) -> None:
    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("centric_api.cli.run_status", interrupt)

    assert main(["status"]) == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Interrupted.\n"
