from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from centric_api.config import ConfigError
from centric_api.units import load_unit_registry, parse_unit_registry


def test_unit_registry_converts_and_normalizes_defaults() -> None:
    registry = load_unit_registry("config/units.yml")

    assert registry.normalize("sq m").unit == "m2"
    assert registry.normalize("sq ft").unit == "ft2"
    assert registry.normalize("pounds").unit == "lb"
    conversion = registry.convert("1500", "g", "kg")

    assert conversion.dimension == "mass"
    assert conversion.from_unit == "g"
    assert conversion.to_unit == "kg"
    assert conversion.output_value.normalize() == Decimal("1.5")
    assert str(registry.convert("1", "lb", "oz").output_value) == "16"
    assert str(registry.convert("1", "yd", "ft").output_value) == "3"
    assert str(registry.convert("1", "gal", "l").output_value) == "3.785411784"
    assert registry.dimensions["mass"].base == "kg"


def test_unit_registry_rejects_incompatible_units() -> None:
    registry = load_unit_registry("config/units.yml")

    with pytest.raises(ValueError, match="Incompatible units"):
        registry.convert("1", "pcs", "kg")


def test_unit_registry_private_overlay_extends_defaults(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    (home / "units.yml").write_text(
        """
version: 1
dimensions:
  mass:
    units:
      lb:
        factor: 0.45359237
        aliases: [lbs, pound, pounds]
      kg:
        aliases: [kilo]
""",
        encoding="utf-8",
    )

    registry = load_unit_registry()

    assert registry.normalize("kilo").unit == "kg"
    assert str(registry.convert("1", "lb", "g").output_value) == "453.59237"


def test_unit_registry_rejects_alias_conflicts() -> None:
    payload = {
        "version": 1,
        "dimensions": {
            "mass": {
                "base": "g",
                "units": {
                    "g": {"factor": 1, "aliases": ["unit"]},
                },
            },
            "count": {
                "base": "pcs",
                "units": {
                    "pcs": {"factor": 1, "aliases": ["unit"]},
                },
            },
        },
    }

    with pytest.raises(ConfigError, match="Unit alias"):
        parse_unit_registry(payload, path=Path("units.yml"))
