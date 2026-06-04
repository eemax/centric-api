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
    assert str(registry.convert("150", "gsm", "kg_per_m2").output_value) == "0.150"
    assert str(registry.convert("1", "tex", "g_per_m").output_value) == "0.001"
    assert registry.dimensions["mass"].base == "kg"


def test_unit_registry_resolves_consumption_basis() -> None:
    registry = load_unit_registry("config/units.yml")

    mass_basis = registry.basis("kg")
    assert mass_basis.consumption.basis == "direct_mass"
    assert mass_basis.consumption.material_value == "ignored"

    count_basis = registry.basis("pcs")
    assert count_basis.consumption.basis == "per_piece_mass"
    assert count_basis.consumption.material_value_unit == "g"

    fabric_basis = registry.basis("gsm")
    assert fabric_basis.unit == "g_per_m2"
    assert fabric_basis.consumption.basis == "areal_density"
    assert fabric_basis.consumption.requires == (
        "bom_quantity",
        "material_uom",
        "material_weight",
        "cuttable_width",
    )
    assert fabric_basis.unit_context is not None
    assert fabric_basis.unit_context.bom_quantity_unit == "m"
    assert fabric_basis.unit_context.width_unit == "m"

    thread_basis = registry.basis("g/m")
    assert thread_basis.unit == "g_per_m"
    assert thread_basis.consumption.basis == "linear_density"
    assert thread_basis.consumption.requires == (
        "bom_quantity",
        "material_uom",
        "material_weight",
    )
    assert thread_basis.unit_context is not None
    assert thread_basis.unit_context.bom_quantity_unit == "m"

    yards_basis = registry.basis("oz/yd2")
    assert yards_basis.unit_context is not None
    assert yards_basis.unit_context.bom_quantity_unit == "yd"
    assert yards_basis.unit_context.width_unit == "yd"


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


def test_unit_registry_private_overlay_replaces_basis_units(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CENTRIC_API_HOME", str(home))
    (home / "units.yml").write_text(
        """
version: 1
dimensions:
  areal_density:
    units:
      g_per_m2:
        basis_units:
          bom_quantity_unit: yd
          width_unit: yd
""",
        encoding="utf-8",
    )

    registry = load_unit_registry()
    basis = registry.basis("gsm")

    assert basis.unit_context is not None
    assert basis.unit_context.bom_quantity_unit == "yd"
    assert basis.unit_context.width_unit == "yd"


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


def test_unit_registry_rejects_unknown_consumption_unit_references() -> None:
    payload = {
        "version": 1,
        "dimensions": {
            "mass": {
                "base": "kg",
                "consumption": {
                    "basis": "direct_mass",
                    "bom_quantity": "mass",
                    "material_value": "ignored",
                    "output_unit": "stone",
                    "requires": [],
                    "formula": "qty",
                },
                "units": {
                    "kg": {"factor": 1},
                },
            },
        },
    }

    with pytest.raises(ConfigError, match="consumption.output_unit references unknown unit"):
        parse_unit_registry(payload, path=Path("units.yml"))


def test_unit_registry_rejects_unknown_basis_unit_references() -> None:
    payload = {
        "version": 1,
        "dimensions": {
            "areal_density": {
                "base": "kg_per_m2",
                "units": {
                    "kg_per_m2": {
                        "factor": 1,
                        "basis_units": {
                            "bom_quantity_unit": "furlong",
                            "width_unit": "m",
                        },
                    },
                },
            },
            "length": {
                "base": "m",
                "units": {
                    "m": {"factor": 1},
                },
            },
        },
    }

    with pytest.raises(ConfigError, match="basis_units.bom_quantity_unit"):
        parse_unit_registry(payload, path=Path("units.yml"))
