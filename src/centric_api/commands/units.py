from __future__ import annotations

import argparse
import json

from ..units import (
    UnitConversion,
    UnitRegistry,
    decimal_json_value,
    decimal_value,
    format_decimal,
    load_unit_registry,
)


def run_units(args: argparse.Namespace) -> int:
    registry = load_unit_registry(args.units_config)
    if args.action == "list":
        payload = _list_record(registry)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_human_units_list(registry)
        return 0
    if args.action == "show":
        dimension = registry.dimensions.get(args.dimension)
        if dimension is None:
            names = ", ".join(sorted(registry.dimensions))
            raise ValueError(f"Unknown unit dimension {args.dimension!r}. Available: {names}")
        payload = _dimension_record(registry, args.dimension)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_human_unit_dimension(registry, args.dimension)
        return 0
    if args.action == "normalize":
        normalized = registry.normalize(args.unit)
        payload = {
            "input": args.unit,
            "unit": normalized.unit,
            "dimension": normalized.dimension,
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print(f"{args.unit} -> {normalized.unit} ({normalized.dimension})")
        return 0
    if args.action == "convert":
        conversion = registry.convert(
            decimal_value(args.value, field_name="value"),
            args.from_unit,
            args.to_unit,
        )
        payload = _conversion_record(conversion)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print(
                f"{format_decimal(conversion.input_value)} {conversion.from_unit} = "
                f"{format_decimal(conversion.output_value)} {conversion.to_unit} "
                f"({conversion.dimension})"
            )
        return 0
    if args.action == "check":
        payload = _list_record(registry)
        if args.json:
            print(json.dumps({"status": "ok", **payload}, default=str))
        else:
            unit_count = sum(len(dimension.units) for dimension in registry.dimensions.values())
            print("Units OK")
            print()
            print(f"Config:     {registry.path}")
            print(f"Dimensions: {len(registry.dimensions)}")
            print(f"Units:      {unit_count}")
        return 0
    return 0


def _list_record(registry: UnitRegistry) -> dict[str, object]:
    return {
        "config": str(registry.path),
        "dimensions": [_dimension_record(registry, name) for name in sorted(registry.dimensions)],
    }


def _dimension_record(registry: UnitRegistry, name: str) -> dict[str, object]:
    dimension = registry.dimensions[name]
    return {
        "dimension": dimension.name,
        "base": dimension.base,
        "units": [
            {
                "unit": unit.unit,
                "factor": decimal_json_value(unit.factor),
                "aliases": list(unit.aliases),
            }
            for unit in dimension.units.values()
        ],
    }


def _conversion_record(conversion: UnitConversion) -> dict[str, object]:
    return {
        "input_value": decimal_json_value(conversion.input_value),
        "value": decimal_json_value(conversion.output_value),
        "from_unit": conversion.from_unit,
        "to_unit": conversion.to_unit,
        "dimension": conversion.dimension,
    }


def _print_human_units_list(registry: UnitRegistry) -> None:
    print("Units")
    print()
    print(f"Config:     {registry.path}")
    print(f"Dimensions: {len(registry.dimensions)}")
    print()
    header = f"{'Dimension':<12}  {'Base':<8}  Units"
    print(header)
    print("-" * len(header))
    for name in sorted(registry.dimensions):
        dimension = registry.dimensions[name]
        units = ", ".join(dimension.units)
        print(f"{dimension.name:<12}  {dimension.base:<8}  {units}")


def _print_human_unit_dimension(registry: UnitRegistry, name: str) -> None:
    dimension = registry.dimensions[name]
    print(f"Unit Dimension: {dimension.name}")
    print()
    print(f"Base: {dimension.base}")
    print()
    header = f"{'Unit':<8}  {'Factor':>12}  Aliases"
    print(header)
    print("-" * len(header))
    for unit in dimension.units.values():
        aliases = ", ".join(unit.aliases)
        print(f"{unit.unit:<8}  {format_decimal(unit.factor):>12}  {aliases}")
