from __future__ import annotations

import json
from typing import Any

from ..view_config import ViewDefinition
from ..view_export import ViewExportResult
from .common import format_count


def view_record(view: ViewDefinition) -> dict[str, Any]:
    return {
        "name": view.name,
        "title": view.title,
        "root": {"endpoint": view.root.endpoint, "as": view.root.alias},
        "joins": [
            {
                "as": join.alias,
                "endpoint": join.endpoint,
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
        "warnings": list(result.warnings),
    }


def print_human_view_list(views: tuple[ViewDefinition, ...]) -> None:
    print("Configured Views")
    print()
    print(f"Views: {format_count(len(views))}")
    print()
    name_width = max(len("Name"), *(len(view.name) for view in views))
    root_width = max(len("Root"), *(len(view.root.endpoint) for view in views))
    header = f"{'Name':<{name_width}}  {'Root':<{root_width}}  Columns  Title"
    print(header)
    print("-" * len(header))
    for view in views:
        print(
            f"{view.name:<{name_width}}  "
            f"{view.root.endpoint:<{root_width}}  "
            f"{format_count(len(view.columns)):>7}  "
            f"{view.title}"
        )


def print_human_view_show(view: ViewDefinition) -> None:
    print(f"View: {view.name}")
    print()
    print(f"Title: {view.title}")
    print(f"Root:  {view.root.endpoint} as {view.root.alias}")
    print()
    if view.joins:
        print("Joins")
        alias_width = max(len("Alias"), *(len(join.alias) for join in view.joins))
        endpoint_width = max(len("Endpoint"), *(len(join.endpoint) for join in view.joins))
        relationship_width = max(
            len("Relationship"),
            *(len(join.relationship) for join in view.joins),
        )
        header = (
            f"  {'Alias':<{alias_width}}  {'Endpoint':<{endpoint_width}}  "
            f"{'Relationship':<{relationship_width}}  From -> To"
        )
        print(header)
        print(f"  {'-' * (len(header) - 2)}")
        for join in view.joins:
            print(
                f"  {join.alias:<{alias_width}}  "
                f"{join.endpoint:<{endpoint_width}}  "
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
    print(f"Missing joins: {format_count(result.missing_join_count)}")
    print(f"Format:        {result.format}")
    print(f"File:          {result.output_path}")
    if result.warnings:
        print()
        print("Warnings")
        for warning in result.warnings[:10]:
            print(f"  {warning}")
        hidden_count = len(result.warnings) - 10
        if hidden_count > 0:
            print(f"  ... {hidden_count} more warning{'' if hidden_count == 1 else 's'}")


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


def _filter_label(item: Any) -> str:
    record = _filter_record(item)
    operator = str(record.pop("operator"))
    path = str(record.pop("path"))
    value = next(iter(record.values()), None)
    return f"{path} {operator} {json.dumps(value, default=str)}"
