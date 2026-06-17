from __future__ import annotations

from typing import Any

from ..swagger.inspection import (
    diff_count,
    field_diff,
    operation_diff,
    operation_field_groups,
)


def print_diff_summary(
    diff: dict[str, Any],
    *,
    fields_only: bool = False,
    operations_only: bool = False,
) -> None:
    if not operations_only:
        fields = field_diff(diff)
        print(
            "Field drift:       "
            f"+{fields.get('added_count', 0)} "
            f"-{fields.get('removed_count', 0)} "
            f"~{fields.get('changed_count', 0)}"
        )
    if not fields_only:
        operations = operation_diff(diff)
        print(
            "Operation drift:   "
            f"+{operations.get('added_count', 0)} "
            f"-{operations.get('removed_count', 0)} "
            f"~{operations.get('changed_count', 0)}"
        )
    if not diff_count(diff, fields_only=fields_only, operations_only=operations_only):
        print("No Swagger drift.")


def drift_summary_text(diff: dict[str, Any]) -> str:
    fields = field_diff(diff)
    operations = operation_diff(diff)
    return (
        f"fields +{fields.get('added_count', 0)} -{fields.get('removed_count', 0)} "
        f"~{fields.get('changed_count', 0)}; "
        f"operations +{operations.get('added_count', 0)} "
        f"-{operations.get('removed_count', 0)} ~{operations.get('changed_count', 0)}"
    )


def print_coverage(report: dict[str, Any]) -> None:
    print("Swagger Coverage")
    print()
    print(f"Configured fetch endpoints:      {report['configured_count']}")
    print(f"Swagger GET collections:         {report['swagger_get_collection_count']}")
    print(f"Covered:                         {report['covered_count']}")
    print(f"Configured but missing Swagger:  {report['missing_in_swagger_count']}")
    print(f"Swagger only:                    {report['missing_in_config_count']}")
    print(f"Swagger only with POST:          {report['swagger_only_post_count']}")
    _print_coverage_rows("Covered", report["covered"])
    _print_missing_configured(report["missing_in_swagger"])
    _print_coverage_rows(
        "Swagger Only: Most Field-Rich / POST-Capable", report["missing_in_config"]
    )


def print_history(rows: list[dict[str, Any]]) -> None:
    _print_table(
        ["Index", "Snapshot", "Fetched at", "Ops", "Endpoints", "Schemas", "Fields", "SHA-256"],
        [
            [
                row["index"],
                row["snapshot_id"],
                row.get("fetched_at") or "",
                row.get("operation_count") or "",
                row.get("endpoint_count") or "",
                row.get("field_schema_count") or "",
                row.get("field_count") or "",
                _short_sha(row.get("sha256")),
            ]
            for row in rows
        ],
        right_align={0, 3, 4, 5, 6},
        max_widths={1: 24, 2: 32, 7: 12},
    )


def print_history_diffs(rows: list[dict[str, Any]]) -> None:
    _print_table(
        [
            "Current",
            "Baseline",
            "Current snapshot",
            "Baseline snapshot",
            "Field +",
            "Field -",
            "Field ~",
            "Op +",
            "Op -",
            "Op ~",
        ],
        [
            [
                row["current_index"],
                row["baseline_index"],
                row["current_snapshot_id"],
                row["baseline_snapshot_id"],
                row["field_added_count"],
                row["field_removed_count"],
                row["field_changed_count"],
                row["operation_added_count"],
                row["operation_removed_count"],
                row["operation_changed_count"],
            ]
            for row in rows
        ],
        right_align={0, 1, 4, 5, 6, 7, 8, 9},
        max_widths={2: 24, 3: 24},
    )


def print_endpoints(rows: list[dict[str, Any]]) -> None:
    table_rows = [
        [
            row["method"],
            row["path"],
            row["request_field_count"],
            row["request_schema"] or "",
            row["response_field_count"],
            row["response_schema"] or "",
        ]
        for row in rows
    ]
    _print_table(
        ["Method", "Path", "Req fields", "Request schema", "Resp fields", "Response schema"],
        table_rows,
        right_align={2, 4},
        max_widths={1: 64, 3: 36, 5: 36},
    )


def _print_coverage_rows(title: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    print()
    print(title)
    table_rows = [
        [
            row["swagger_path"],
            row["response_field_count"],
            row["response_schema"] or "",
            row["post_field_count"] if row.get("has_post") else "",
            row["post_schema"] or "",
        ]
        for row in rows
    ]
    _print_table(
        ["Path", "Get fields", "Get schema", "Post fields", "Post schema"],
        table_rows,
        right_align={1, 3},
        max_widths={0: 52, 2: 40, 4: 40},
    )


def _print_missing_configured(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    print()
    print("Configured But Missing In Swagger")
    _print_table(
        ["Endpoint", "Configured path"],
        [[row["name"], row["configured_path"]] for row in rows],
        max_widths={0: 40, 1: 72},
    )


def print_field_diff_details(diff: dict[str, Any]) -> None:
    fields = field_diff(diff)
    groups = fields.get("groups", [])
    if not groups:
        return
    print()
    print("Fields")
    for group in groups:
        header = (
            f"{group['endpoint']} {group['method']} {group['surface']} "
            f"{group['path']} ({group.get('schema') or 'schema unknown'})"
        )
        print(header)
        for field in group.get("added", []):
            print(f"  + {_field_label(field)}")
        for field in group.get("removed", []):
            print(f"  - {_field_label(field)}")
        for field in group.get("changed", []):
            changes = ", ".join(
                _field_change_label(name, value) for name, value in field.get("changes", {}).items()
            )
            label = f"{field['name']} {changes}"
            print(f"  ~ {label}")
            enum_change = field.get("changes", {}).get("enum")
            if isinstance(enum_change, dict):
                _print_enum_change_details(enum_change)


def print_operation_diff_details(diff: dict[str, Any]) -> None:
    operations = operation_diff(diff)
    if (
        not operations.get("added")
        and not operations.get("removed")
        and not operations.get("changed")
    ):
        return
    print()
    print("Operations")
    for label, key in (("added", "added"), ("removed", "removed"), ("changed", "changed")):
        for row in operations.get(key, []):
            print(f"- {label}: {row['method']} {row['path']}")


def print_field_summary(operations: list[Any], *, required_only: bool = False) -> None:
    print("Swagger Field Schemas")
    table_rows = []
    for operation in operations:
        for surface, schema, fields in operation_field_groups(
            operation, required_only=required_only
        ):
            table_rows.append(
                [
                    operation.method,
                    operation.path,
                    surface,
                    len(fields),
                    sum(1 for field in fields if field.required),
                    schema or "",
                ]
            )
    _print_table(
        ["Method", "Path", "Surface", "Fields", "Required", "Schema"],
        table_rows,
        right_align={3, 4},
        max_widths={1: 64, 5: 48},
    )


def print_field_details(rows: list[dict[str, Any]]) -> None:
    print("Swagger Fields")
    for group in _field_detail_groups(rows):
        required_count = sum(1 for row in group["rows"] if row["required"])
        print()
        print(
            f"{group['method']} {group['path']} {group['surface']} "
            f"({group['schema'] or 'schema unknown'}): "
            f"{len(group['rows'])} fields, {required_count} required"
        )
        _print_table(
            ["Index", "Required", "Field", "Type", "Target", "Description"],
            [
                [
                    row["index"],
                    "yes" if row["required"] else "",
                    row["name"],
                    _field_type_label(row),
                    _field_target(row),
                    _display_description(row) or "",
                ]
                for row in group["rows"]
            ],
            right_align={0},
            max_widths={2: 40, 3: 32, 4: 24, 5: 64},
            wrap_columns={5},
        )


def _field_detail_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["method"], row["path"], row["surface"], row["schema"])
        by_key.setdefault(key, []).append(row)
    for method, path, surface, schema in by_key:
        groups.append(
            {
                "method": method,
                "path": path,
                "surface": surface,
                "schema": schema,
                "rows": by_key[(method, path, surface, schema)],
            }
        )
    return groups


def print_field_inspection(rows: list[dict[str, Any]]) -> None:
    for row_index, row in enumerate(rows):
        if row_index:
            print()
        print(f"Index:      {row['index']}")
        print(f"Endpoint:   {row['endpoint']}")
        print(f"Method:     {row['method']}")
        print(f"Path:       {row['path']}")
        print(f"Surface:    {row['surface']}")
        print(f"Schema:     {row['schema'] or 'schema unknown'}")
        print(f"Field:      {row['name']}")
        print(f"Type:       {_field_scalar_type_label(row)}")
        print(f"Required:   {'yes' if row['required'] else 'no'}")
        target = _field_target(row)
        if target:
            print(f"Target:     {target}")
        description = _display_description(row)
        if description:
            print(f"Description: {description}")
        enum_values = row.get("enum")
        if enum_values:
            print(f"Enum:       {len(enum_values)} values")
            print()
            for enum_index, value in enumerate(enum_values, start=1):
                print(f"{enum_index}. {value}")


def _field_label(field: dict[str, Any]) -> str:
    type_label = _field_type_label(field)
    target = _field_target(field)
    if target:
        type_label = f"{type_label} -> {target}"
    required = " required" if field.get("required") else ""
    description = _display_description(field)
    suffix = f" - {description}" if description else ""
    return f"{field['name']} {type_label}{required}{suffix}"


def _field_type_label(field: dict[str, Any]) -> str:
    type_label = _field_scalar_type_label(field)
    enum_values = field.get("enum")
    if enum_values:
        type_label = f"{type_label} enum={list(enum_values)}"
    return type_label


def _field_scalar_type_label(field: dict[str, Any]) -> str:
    type_label = field.get("type") or "unknown"
    if field.get("item_type"):
        type_label = f"{type_label}[{field['item_type']}]"
    if field.get("format"):
        type_label = f"{type_label} ({field['format']})"
    return type_label


def _field_target(field: dict[str, Any]) -> str:
    return str(field.get("object_type") or field.get("ref") or "")


def _display_description(field: dict[str, Any]) -> str | None:
    description = field.get("description")
    if not isinstance(description, str):
        return None
    if field.get("object_type") and "Object Type:" in description:
        description = description.split("Object Type:", 1)[0]
    return description.strip() or None


def _diff_value(value: Any) -> str:
    if value is None:
        return "None"
    return str(value)


def _field_change_label(name: str, value: Any) -> str:
    if name == "enum" and isinstance(value, dict):
        return f"enum +{int(value.get('added_count', 0))} -{int(value.get('removed_count', 0))}"
    if isinstance(value, dict) and "from" in value and "to" in value:
        return f"{name}: {_diff_value(value['from'])} -> {_diff_value(value['to'])}"
    return f"{name}: {_diff_value(value)}"


def _print_enum_change_details(change: dict[str, Any]) -> None:
    for value in change.get("added", []):
        print(f"      + {value}")
    for value in change.get("removed", []):
        print(f"      - {value}")


def _print_table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    right_align: set[int] | None = None,
    max_widths: dict[int, int] | None = None,
    wrap_columns: set[int] | None = None,
) -> None:
    if not rows:
        return
    right_align = right_align or set()
    max_widths = max_widths or {}
    wrap_columns = wrap_columns or set()
    text_rows = [[_table_cell(value) for value in row] for row in rows]
    widths = [
        max(
            len(header),
            min(
                max_widths.get(index, 10_000),
                max(len(row[index]) for row in text_rows),
            ),
        )
        for index, header in enumerate(headers)
    ]
    print(_format_table_row(headers, widths, right_align=set()))
    print(_format_table_row(["-" * width for width in widths], widths, right_align=set()))
    for row in text_rows:
        for line in _table_display_lines(row, widths, wrap_columns=wrap_columns):
            print(_format_table_row(line, widths, right_align=right_align))


def _table_display_lines(
    row: list[str],
    widths: list[int],
    *,
    wrap_columns: set[int],
) -> list[list[str]]:
    wrapped = [
        _wrap_cell(value, widths[index]) if index in wrap_columns else [value]
        for index, value in enumerate(row)
    ]
    line_count = max(len(lines) for lines in wrapped)
    lines = []
    for line_index in range(line_count):
        lines.append(
            [
                column_lines[line_index] if line_index < len(column_lines) else ""
                for column_lines in wrapped
            ]
        )
    return lines


def _format_table_row(row: list[str], widths: list[int], *, right_align: set[int]) -> str:
    cells = []
    for index, value in enumerate(row):
        width = widths[index]
        value = _truncate_cell(value, width)
        cells.append(value.rjust(width) if index in right_align else value.ljust(width))
    return "  ".join(cells).rstrip()


def _truncate_cell(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    return f"{value[: width - 3]}..."


def _wrap_cell(value: str, width: int) -> list[str]:
    if len(value) <= width:
        return [value]
    if width <= 3:
        return [_truncate_cell(value, width)]
    lines = []
    remaining = value
    while len(remaining) > width:
        split_at = remaining.rfind(" ", 0, width + 1)
        if split_at <= 0:
            split_at = width
        lines.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        lines.append(remaining)
    return lines or [""]


def _table_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _short_sha(value: Any) -> str:
    return str(value)[:12] if value else ""
