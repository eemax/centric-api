from __future__ import annotations

import argparse
import json

from ..units import (
    ConsumptionBasis,
    ConsumptionUnitContext,
    UnitBasis,
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
    if args.action == "basis":
        basis = registry.basis(args.unit)
        payload = _basis_record(basis)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            _print_human_basis(basis)
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
    record: dict[str, object] = {
        "dimension": dimension.name,
        "base": dimension.base,
        "units": [
            {
                "unit": unit.unit,
                "factor": decimal_json_value(unit.factor),
                "aliases": list(unit.aliases),
                **_unit_context_record(unit.basis_units),
            }
            for unit in dimension.units.values()
        ],
    }
    if dimension.consumption is not None:
        record["consumption"] = _consumption_record(dimension.consumption)
    return record


def _conversion_record(conversion: UnitConversion) -> dict[str, object]:
    return {
        "input_value": decimal_json_value(conversion.input_value),
        "value": decimal_json_value(conversion.output_value),
        "from_unit": conversion.from_unit,
        "to_unit": conversion.to_unit,
        "dimension": conversion.dimension,
    }


def _basis_record(basis: UnitBasis) -> dict[str, object]:
    return {
        "input": basis.input,
        "unit": basis.unit,
        "dimension": basis.dimension,
        **_consumption_record(basis.consumption),
        **_unit_context_record(basis.unit_context),
    }


def _consumption_record(consumption: ConsumptionBasis) -> dict[str, object]:
    record: dict[str, object] = {
        "basis": consumption.basis,
        "bom_quantity": consumption.bom_quantity,
        "material_value": consumption.material_value,
        "output_unit": consumption.output_unit,
        "requires": list(consumption.requires),
        "formula": consumption.formula,
    }
    if consumption.material_value_unit is not None:
        record["material_value_unit"] = consumption.material_value_unit
    return record


def _unit_context_record(context: ConsumptionUnitContext | None) -> dict[str, object]:
    if context is None:
        return {}
    return {
        key: value
        for key, value in {
            "bom_quantity_unit": context.bom_quantity_unit,
            "width_unit": context.width_unit,
        }.items()
        if value is not None
    }


def _print_human_units_list(registry: UnitRegistry) -> None:
    print("Units")
    print()
    print(f"Config:     {registry.path}")
    print(f"Dimensions: {len(registry.dimensions)}")
    print()
    header = f"{'Dimension':<16}  {'Base':<12}  {'Basis':<16}  Units"
    print(header)
    print("-" * len(header))
    for name in sorted(registry.dimensions):
        dimension = registry.dimensions[name]
        basis = dimension.consumption.basis if dimension.consumption else "-"
        units = ", ".join(dimension.units)
        print(f"{dimension.name:<16}  {dimension.base:<12}  {basis:<16}  {units}")


def _print_human_unit_dimension(registry: UnitRegistry, name: str) -> None:
    dimension = registry.dimensions[name]
    print(f"Unit Dimension: {dimension.name}")
    print()
    print(f"Base: {dimension.base}")
    if dimension.consumption is not None:
        print(f"Basis: {dimension.consumption.basis}")
    print()
    header = f"{'Unit':<8}  {'Factor':>12}  Aliases"
    print(header)
    print("-" * len(header))
    for unit in dimension.units.values():
        aliases = ", ".join(unit.aliases)
        print(f"{unit.unit:<8}  {format_decimal(unit.factor):>12}  {aliases}")


def _print_human_basis(basis: UnitBasis) -> None:
    consumption = basis.consumption
    print("Consumption Basis")
    print()
    print(f"Unit:           {basis.input} -> {basis.unit} ({basis.dimension})")
    print(f"Basis:          {consumption.basis}")
    print(f"BOM quantity:   {consumption.bom_quantity}")
    print(f"Material value: {consumption.material_value}")
    if consumption.material_value_unit is not None:
        print(f"Material unit:  {consumption.material_value_unit}")
    if basis.unit_context is not None and basis.unit_context.bom_quantity_unit is not None:
        print(f"BOM unit:       {basis.unit_context.bom_quantity_unit}")
    if basis.unit_context is not None and basis.unit_context.width_unit is not None:
        print(f"Width unit:     {basis.unit_context.width_unit}")
    if consumption.requires:
        print(f"Requires:       {', '.join(consumption.requires)}")
    print(f"Output:         {consumption.output_unit}")
    print(f"Formula:        {consumption.formula}")
