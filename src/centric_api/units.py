from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, runtime_home

DEFAULT_UNITS_CONFIG_PATH = Path("config/units.yml")
PRIVATE_UNITS_CONFIG_PATH = Path("units.yml")
ROOT_CONFIG_KEYS = {"version", "dimensions"}
DIMENSION_CONFIG_KEYS = {"base", "units"}
UNIT_CONFIG_KEYS = {"factor", "aliases"}


class UnitError(ValueError):
    pass


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    unit: str
    factor: Decimal
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class UnitDimension:
    name: str
    base: str
    units: dict[str, UnitDefinition]


@dataclass(frozen=True)
class NormalizedUnit:
    input: str
    unit: str
    dimension: str


@dataclass(frozen=True)
class UnitConversion:
    input_value: Decimal
    output_value: Decimal
    from_unit: str
    to_unit: str
    dimension: str


@dataclass(frozen=True)
class UnitRegistry:
    path: Path
    dimensions: dict[str, UnitDimension]
    aliases: dict[str, NormalizedUnit]

    def normalize(self, unit: str) -> NormalizedUnit:
        key = _alias_key(unit)
        if key not in self.aliases:
            raise UnitError(f"Unknown unit: {unit!r}.")
        normalized = self.aliases[key]
        return NormalizedUnit(input=unit, unit=normalized.unit, dimension=normalized.dimension)

    def convert(
        self,
        value: Decimal | int | float | str,
        from_unit: str,
        to_unit: str,
    ) -> UnitConversion:
        input_value = decimal_value(value, field_name="value")
        source = self.normalize(from_unit)
        target = self.normalize(to_unit)
        if source.dimension != target.dimension:
            raise UnitError(
                "Incompatible units: "
                f"{source.unit} is {source.dimension}, {target.unit} is {target.dimension}."
            )
        dimension = self.dimensions[source.dimension]
        source_factor = dimension.units[source.unit].factor
        target_factor = dimension.units[target.unit].factor
        output_value = (input_value * source_factor) / target_factor
        return UnitConversion(
            input_value=input_value,
            output_value=output_value,
            from_unit=source.unit,
            to_unit=target.unit,
            dimension=source.dimension,
        )


def resolve_units_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    private_path = runtime_home() / PRIVATE_UNITS_CONFIG_PATH
    if private_path.is_file():
        return private_path
    return DEFAULT_UNITS_CONFIG_PATH


def load_unit_registry(path: str | Path | None = None) -> UnitRegistry:
    config_path = resolve_units_config_path(path)
    if path is None:
        payload = _load_payload(DEFAULT_UNITS_CONFIG_PATH)
        private_path = runtime_home() / PRIVATE_UNITS_CONFIG_PATH
        if private_path.is_file():
            payload = _merge_payloads(payload, _load_payload(private_path))
            config_path = private_path
    else:
        payload = _load_payload(config_path)
    return parse_unit_registry(payload, path=config_path)


def parse_unit_registry(payload: dict[str, Any], *, path: Path) -> UnitRegistry:
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "units config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("units config version must be 1.")
    dimensions_raw = payload.get("dimensions")
    if not isinstance(dimensions_raw, dict) or not dimensions_raw:
        raise ConfigError("units config dimensions must be a non-empty object.")

    dimensions: dict[str, UnitDimension] = {}
    aliases: dict[str, NormalizedUnit] = {}
    for dimension_name, raw_dimension in sorted(dimensions_raw.items()):
        dimension = _parse_dimension(dimension_name, raw_dimension)
        dimensions[dimension.name] = dimension
        for unit in dimension.units.values():
            _register_alias(aliases, unit.unit, unit.unit, unit.dimension)
            for alias in unit.aliases:
                _register_alias(aliases, alias, unit.unit, unit.dimension)

    return UnitRegistry(path=path, dimensions=dimensions, aliases=aliases)


def decimal_value(value: Decimal | int | float | str, *, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise UnitError(f"{field_name} must be numeric.") from exc
    if not parsed.is_finite():
        raise UnitError(f"{field_name} must be finite.")
    return parsed


def format_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f").rstrip("0").rstrip(".")


def decimal_json_value(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Units config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError("Units config root must be an object.")
    return payload


def _merge_payloads(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_keys(overlay, ROOT_CONFIG_KEYS, "units config")
    merged = dict(base)
    if "version" in overlay:
        merged["version"] = overlay["version"]
    base_dimensions = base.get("dimensions", {})
    overlay_dimensions = overlay.get("dimensions", {})
    if not isinstance(base_dimensions, dict) or not isinstance(overlay_dimensions, dict):
        raise ConfigError("units config dimensions must be an object.")
    dimensions = {name: dict(value) for name, value in base_dimensions.items()}
    for dimension_name, raw_overlay_dimension in overlay_dimensions.items():
        if not isinstance(raw_overlay_dimension, dict):
            raise ConfigError(f"units dimension[{dimension_name}] must be an object.")
        _reject_unknown_keys(
            raw_overlay_dimension,
            DIMENSION_CONFIG_KEYS,
            f"units dimension[{dimension_name}]",
        )
        if dimension_name not in dimensions:
            dimensions[dimension_name] = dict(raw_overlay_dimension)
            continue
        raw_base_dimension = dimensions[dimension_name]
        overlay_base = raw_overlay_dimension.get("base")
        if overlay_base is not None and overlay_base != raw_base_dimension.get("base"):
            raise ConfigError(
                f"units dimension[{dimension_name}].base cannot override "
                f"{raw_base_dimension.get('base')!r} with {overlay_base!r}."
            )
        base_units = raw_base_dimension.get("units", {})
        overlay_units = raw_overlay_dimension.get("units", {})
        if not isinstance(base_units, dict) or not isinstance(overlay_units, dict):
            raise ConfigError(f"units dimension[{dimension_name}].units must be an object.")
        units = {unit_name: dict(unit) for unit_name, unit in base_units.items()}
        for unit_name, raw_overlay_unit in overlay_units.items():
            if not isinstance(raw_overlay_unit, dict):
                raise ConfigError(
                    f"units dimension[{dimension_name}].units[{unit_name}] must be an object."
                )
            _reject_unknown_keys(
                raw_overlay_unit,
                UNIT_CONFIG_KEYS,
                f"units dimension[{dimension_name}].units[{unit_name}]",
            )
            if unit_name not in units:
                units[unit_name] = dict(raw_overlay_unit)
                continue
            unit = dict(units[unit_name])
            if "factor" in raw_overlay_unit:
                unit["factor"] = raw_overlay_unit["factor"]
            if "aliases" in raw_overlay_unit:
                existing_aliases = unit.get("aliases", [])
                overlay_aliases = raw_overlay_unit["aliases"]
                if not isinstance(existing_aliases, list) or not isinstance(overlay_aliases, list):
                    raise ConfigError(f"units[{unit_name}].aliases must be an array.")
                unit["aliases"] = [*existing_aliases, *overlay_aliases]
            units[unit_name] = unit
        raw_base_dimension["units"] = units
    merged["dimensions"] = dimensions
    return merged


def _parse_dimension(name: Any, raw: Any) -> UnitDimension:
    dimension_name = _required_name(name, "dimension name")
    if not isinstance(raw, dict):
        raise ConfigError(f"units dimension[{dimension_name}] must be an object.")
    _reject_unknown_keys(raw, DIMENSION_CONFIG_KEYS, f"units dimension[{dimension_name}]")
    base = _required_name(raw.get("base"), f"units dimension[{dimension_name}].base")
    units_raw = raw.get("units")
    if not isinstance(units_raw, dict) or not units_raw:
        raise ConfigError(f"units dimension[{dimension_name}].units must be a non-empty object.")
    units: dict[str, UnitDefinition] = {}
    for unit_name, raw_unit in sorted(units_raw.items()):
        unit = _parse_unit(dimension_name, unit_name, raw_unit)
        units[unit.unit] = unit
    if base not in units:
        raise ConfigError(f"units dimension[{dimension_name}].base must be defined in units.")
    return UnitDimension(name=dimension_name, base=base, units=units)


def _parse_unit(dimension: str, name: Any, raw: Any) -> UnitDefinition:
    unit_name = _required_name(name, f"units dimension[{dimension}].unit name")
    if not isinstance(raw, dict):
        raise ConfigError(f"units dimension[{dimension}].units[{unit_name}] must be an object.")
    _reject_unknown_keys(raw, UNIT_CONFIG_KEYS, f"units dimension[{dimension}].units[{unit_name}]")
    if "factor" not in raw:
        raise ConfigError(f"units[{unit_name}].factor is required.")
    try:
        factor = decimal_value(raw.get("factor"), field_name=f"units[{unit_name}].factor")
    except UnitError as exc:
        raise ConfigError(str(exc)) from exc
    if factor <= 0:
        raise ConfigError(f"units[{unit_name}].factor must be positive.")
    aliases_raw = raw.get("aliases", [])
    if not isinstance(aliases_raw, list):
        raise ConfigError(f"units[{unit_name}].aliases must be an array.")
    aliases = tuple(_required_name(alias, f"units[{unit_name}].aliases") for alias in aliases_raw)
    return UnitDefinition(dimension=dimension, unit=unit_name, factor=factor, aliases=aliases)


def _register_alias(
    aliases: dict[str, NormalizedUnit],
    alias: str,
    unit: str,
    dimension: str,
) -> None:
    key = _alias_key(alias)
    existing = aliases.get(key)
    if existing is not None and (existing.unit != unit or existing.dimension != dimension):
        raise ConfigError(
            f"Unit alias {alias!r} maps to both {existing.dimension}.{existing.unit} "
            f"and {dimension}.{unit}."
        )
    aliases[key] = NormalizedUnit(input=alias, unit=unit, dimension=dimension)


def _alias_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _required_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _reject_unknown_keys(raw: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{label} has unknown keys: {', '.join(unknown)}.")
