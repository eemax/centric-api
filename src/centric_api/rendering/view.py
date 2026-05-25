from __future__ import annotations

import json
from typing import Any

from ..view_config import ViewDefinition
from ..view_export import ViewCheckResult, ViewExportResult
from .common import format_count


def view_record(view: ViewDefinition) -> dict[str, Any]:
    return {
        "name": view.name,
        "title": view.title,
        "root": {view.root.source_type: view.root.source_name, "as": view.root.alias},
        "joins": [
            {
                "as": join.alias,
                join.source_type: join.source_name,
                "from": join.from_path,
                "to": join.to_path,
                "relationship": join.relationship,
                "missing": join.missing,
                "separator": join.separator,
                "filters": [_filter_record(item) for item in join.filters],
            }
            for join in view.joins
        ],
        "filters": [_filter_record(item) for item in view.filters],
        "columns": [
            {
                "header": column.header,
                "path": column.path,
                "type": column.type,
                "width": column.width,
                "number_format": column.number_format,
            }
            for column in view.columns
        ],
        "options": {
            "missing": view.options.missing,
            "many_separator": view.options.many_separator,
            "freeze_header": view.options.freeze_header,
            "autofilter": view.options.autofilter,
            "autosize": view.options.autosize,
            "sheet_name": view.options.sheet_name,
        },
    }


def export_record(result: ViewExportResult) -> dict[str, Any]:
    return {
        "view": result.view_name,
        "title": result.title,
        "format": result.format,
        "output_path": str(result.output_path),
        "rows": result.row_count,
        "columns": result.column_count,
        "missing_joins": result.missing_join_count,
        "missing_join_details": [
            _missing_join_detail_record(item) for item in result.missing_join_details
        ],
        "warnings": list(result.warnings),
    }


def check_record(result: ViewCheckResult) -> dict[str, Any]:
    return {
        "view": result.view_name,
        "title": result.title,
        "ok": result.missing_join_count == 0 and not result.warnings,
        "rows_scanned": result.root_row_count,
        "rows_projected": result.row_count,
        "columns": result.column_count,
        "missing_joins": result.missing_join_count,
        "missing_join_details": [
            _missing_join_detail_record(item) for item in result.missing_join_details
        ],
        "warnings": list(result.warnings),
    }


def print_human_view_list(views: tuple[ViewDefinition, ...]) -> None:
    print("Configured Views")
    print()
    print(f"Views: {format_count(len(views))}")
    print()
    name_width = max(len("Name"), *(len(view.name) for view in views))
    root_width = max(len("Root"), *(len(_source_label(view.root)) for view in views))
    header = f"{'Name':<{name_width}}  {'Root':<{root_width}}  Columns  Title"
    print(header)
    print("-" * len(header))
    for view in views:
        print(
            f"{view.name:<{name_width}}  "
            f"{_source_label(view.root):<{root_width}}  "
            f"{format_count(len(view.columns)):>7}  "
            f"{view.title}"
        )


def print_human_view_show(view: ViewDefinition) -> None:
    print(f"View: {view.name}")
    print()
    print(f"Title: {view.title}")
    print(f"Root:  {_source_label(view.root)} as {view.root.alias}")
    print()
    if view.joins:
        print("Joins")
        alias_width = max(len("Alias"), *(len(join.alias) for join in view.joins))
        source_width = max(len("Source"), *(len(_source_label(join)) for join in view.joins))
        relationship_width = max(
            len("Relationship"),
            *(len(join.relationship) for join in view.joins),
        )
        header = (
            f"  {'Alias':<{alias_width}}  {'Source':<{source_width}}  "
            f"{'Relationship':<{relationship_width}}  From -> To"
        )
        print(header)
        print(f"  {'-' * (len(header) - 2)}")
        for join in view.joins:
            print(
                f"  {join.alias:<{alias_width}}  "
                f"{_source_label(join):<{source_width}}  "
                f"{join.relationship:<{relationship_width}}  "
                f"{join.from_path} -> {join.to_path}"
            )
        join_filters = [(join.alias, item) for join in view.joins for item in join.filters]
        if join_filters:
            print()
            print("Join Filters")
            alias_width = max(len("Alias"), *(len(alias) for alias, _item in join_filters))
            for alias, item in join_filters:
                print(f"  {alias:<{alias_width}}  {_filter_label(item)}")
        print()
    if view.filters:
        print("Filters")
        for item in view.filters:
            print(f"  {_filter_label(item)}")
        print()
    print("Columns")
    header_width = max(len("Header"), *(len(column.header) for column in view.columns))
    type_width = max(len("Type"), *(len(column.type) for column in view.columns))
    header = f"  {'Header':<{header_width}}  {'Type':<{type_width}}  Path"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for column in view.columns:
        print(f"  {column.header:<{header_width}}  {column.type:<{type_width}}  {column.path}")


def print_human_view_export(result: ViewExportResult) -> None:
    print(f"View exported: {result.view_name}")
    print()
    print(f"Rows:          {format_count(result.row_count)}")
    print(f"Columns:       {format_count(result.column_count)}")
    print(f"Format:        {result.format}")
    print(f"File:          {result.output_path}")
    _print_missing_join_details(result.missing_join_details, result.missing_join_count)
    if result.warnings:
        _print_warnings(result.warnings)


def print_human_view_check(result: ViewCheckResult) -> None:
    print(f"View check: {result.view_name}")
    print()
    print(f"Rows scanned:   {format_count(result.root_row_count)}")
    print(f"Rows projected: {format_count(result.row_count)}")
    print(f"Columns:        {format_count(result.column_count)}")
    status = "ok" if result.missing_join_count == 0 and not result.warnings else "attention needed"
    print(f"Status:         {status}")
    _print_missing_join_details(result.missing_join_details, result.missing_join_count)
    if result.warnings:
        _print_warnings(result.warnings)


def _print_missing_join_details(details: tuple[Any, ...], missing_count: int) -> None:
    if not details:
        return
    print()
    print(f"Missing refs: {format_count(missing_count)}")
    for item in details:
        print()
        print(f"  {item.alias} -> {_source_label(item)}")
        print(f"    join:    {_missing_join_path(item)}")
        print(f"    missing: {_missing_join_summary(item)}")
        for category in _missing_join_extra_categories(item):
            print(f"    {category}")
        if item.missing_endpoint:
            if getattr(item, "source_type", "endpoint") == "table":
                print(f"    table missing: run the model that creates {item.source_name}")
            else:
                print(f"    endpoint cache empty: fetch {item.source_name}")
        if item.sample_keys and item.missing_ref_count:
            print(f"    samples: {', '.join(item.sample_keys[:3])}")
        if item.filters_applied and item.filtered_out_count:
            print("    join filters excluded some matching records")


def _print_warnings(warnings: tuple[str, ...]) -> None:
    print()
    print("Warnings")
    for warning in warnings[:10]:
        print(f"  {warning}")
    hidden_count = len(warnings) - 10
    if hidden_count > 0:
        print(f"  ... {hidden_count} more warning{'' if hidden_count == 1 else 's'}")


def _missing_join_path(item: Any) -> str:
    return f"{item.from_path} -> {item.to_path}"


def _missing_join_summary(item: Any) -> str:
    if item.missing_ref_count == item.missing_count:
        return f"{format_count(item.missing_ref_count)} refs"
    if item.missing_source_count == item.missing_count:
        return f"{format_count(item.missing_source_count)} blank source values"
    if item.filtered_out_count == item.missing_count:
        return f"{format_count(item.filtered_out_count)} filtered out"
    return f"{format_count(item.missing_count)} joins"


def _missing_join_extra_categories(item: Any) -> list[str]:
    populated_categories = sum(
        bool(value)
        for value in (
            item.missing_ref_count,
            item.missing_source_count,
            item.filtered_out_count,
        )
    )
    if populated_categories <= 1:
        return []
    categories = []
    if item.missing_ref_count:
        categories.append(f"refs:    {format_count(item.missing_ref_count)}")
    if item.missing_source_count:
        categories.append(f"blanks:  {format_count(item.missing_source_count)} source values")
    if item.filtered_out_count:
        categories.append(f"filters: {format_count(item.filtered_out_count)} excluded")
    return categories


def _filter_record(item: Any) -> dict[str, Any]:
    payload = {"path": item.path, "operator": item.operator}
    if item.operator == "equals":
        payload["equals"] = item.equals
    elif item.operator == "in":
        payload["in"] = list(item.in_values or ())
    elif item.operator == "contains":
        payload["contains"] = item.contains
    elif item.operator == "matches":
        payload["matches"] = item.matches
    elif item.operator == "exists":
        payload["exists"] = item.exists
    elif item.operator in {"gt", "gte", "lt", "lte"}:
        payload[item.operator] = getattr(item, item.operator)
    return payload


def _missing_join_detail_record(item: Any) -> dict[str, Any]:
    return {
        "alias": item.alias,
        "source_type": item.source_type,
        "source": item.source_name,
        "endpoint": item.source_name if item.source_type == "endpoint" else None,
        "table": item.source_name if item.source_type == "table" else None,
        "from": item.from_path,
        "to": item.to_path,
        "missing": item.missing_count,
        "missing_source_values": item.missing_source_count,
        "missing_refs": item.missing_ref_count,
        "filtered_out": item.filtered_out_count,
        "missing_endpoint": item.missing_endpoint,
        "filters_applied": item.filters_applied,
        "sample_keys": list(item.sample_keys),
    }


def _filter_label(item: Any) -> str:
    record = _filter_record(item)
    operator = str(record.pop("operator"))
    path = str(record.pop("path"))
    value = next(iter(record.values()), None)
    return f"{path} {operator} {json.dumps(value, default=str)}"


def _source_label(item: Any) -> str:
    source_type = getattr(item, "source_type", "endpoint")
    source_name = getattr(item, "source_name", getattr(item, "endpoint", ""))
    return source_name if source_type == "endpoint" else f"table:{source_name}"
