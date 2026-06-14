from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..auth import AuthContext, resolve_credentials
from ..config import load_fetcher_settings
from ..swagger import (
    build_swagger_index,
    coverage_report,
    diff_swagger_indexes,
    load_swagger_document,
    load_swagger_meta,
    resolve_swagger_history_dir,
    resolve_swagger_history_meta_path,
    resolve_swagger_history_path,
    resolve_swagger_meta_path,
    resolve_swagger_path,
    write_swagger_document,
    write_swagger_meta,
)


def run_swagger(args: argparse.Namespace) -> int:
    if args.action == "refresh":
        return _run_refresh(args)
    if args.action == "status":
        return _run_status(args)
    if args.action == "history":
        return _run_history(args)
    if args.action == "endpoints":
        return _run_endpoints(args)
    if args.action == "fields":
        return _run_fields(args)
    if args.action == "field":
        return _run_field(args)
    if args.action == "diff":
        return _run_diff(args)
    if args.action == "coverage":
        return _run_coverage(args)
    return 0


def _run_refresh(args: argparse.Namespace) -> int:
    fetcher_cfg, auth_settings, _endpoint_specs = load_fetcher_settings(args.fetch_config)
    base_url, username, password = resolve_credentials(
        auth_settings,
        env_file=Path(args.env_file).expanduser() if args.env_file else auth_settings.env_file,
    )
    swagger_url = f"{base_url}/api/v2/swagger.json"
    swagger_path = resolve_swagger_path()
    meta_path = resolve_swagger_meta_path()
    previous_document = _try_load_document()

    with AuthContext(
        base_url=base_url,
        username=username,
        password=password,
        timeout=fetcher_cfg.timeout,
    ) as auth_ctx:
        response = auth_ctx.request("GET", swagger_url)
    if response.status_code >= 400:
        raise ValueError(
            f"Swagger refresh failed with status {response.status_code}: {response.text}"
        )
    document = response.json()
    if not isinstance(document, dict):
        raise ValueError("Swagger refresh response root must be an object.")

    current_index = build_swagger_index(document)
    diff = _empty_diff()
    if previous_document is not None:
        diff = diff_swagger_indexes(build_swagger_index(previous_document), current_index)

    fetched_at = _utc_timestamp()
    snapshot_id = _snapshot_id(fetched_at)
    history_path = resolve_swagger_history_path(snapshot_id)
    history_meta_path = resolve_swagger_history_meta_path(snapshot_id)
    meta = _meta_payload(
        document=document,
        index=current_index,
        url=swagger_url,
        swagger_path=swagger_path,
        history_path=history_path,
        snapshot_id=snapshot_id,
        fetched_at=fetched_at,
        diff=diff,
    )
    write_swagger_document(history_path, document)
    write_swagger_meta(history_meta_path, {**meta, "path": str(history_path)})
    write_swagger_document(swagger_path, document)
    write_swagger_meta(meta_path, meta)
    if args.json:
        print(
            json.dumps(
                {
                    "swagger_path": str(swagger_path),
                    "meta_path": str(meta_path),
                    "history_path": str(history_path),
                    "history_meta_path": str(history_meta_path),
                    **meta,
                }
            )
        )
    else:
        print(f"Refreshed Swagger: {swagger_path}")
        print(f"Metadata:          {meta_path}")
        print(f"Snapshot:          {history_path}")
        print(f"Operations:        {current_index.operation_count}")
        print(f"Endpoints:         {len(current_index.endpoints)}")
        _print_diff_summary(diff)
    return 0


def _run_status(args: argparse.Namespace) -> int:
    swagger_path = resolve_swagger_path()
    meta_path = resolve_swagger_meta_path()
    document, swagger_error = _try_load_status_document()
    meta, meta_error = _try_load_status_meta()
    index = build_swagger_index(document) if document is not None else None
    payload = {
        "swagger_path": str(swagger_path),
        "swagger_exists": swagger_path.is_file(),
        "swagger_error": swagger_error,
        "meta_path": str(meta_path),
        "meta_exists": meta_path.is_file(),
        "meta_error": meta_error,
        "swagger_version": index.swagger_version if index else None,
        "operation_count": index.operation_count if index else 0,
        "endpoint_count": len(index.endpoints) if index else 0,
        "field_schema_count": _field_schema_count(index),
        "field_count": _field_count(index),
        "meta": meta,
    }
    if args.json:
        print(json.dumps(payload, default=str))
    else:
        print("Swagger Status")
        print(f"Swagger:    {swagger_path} ({'present' if swagger_path.is_file() else 'missing'})")
        print(f"Metadata:   {meta_path} ({'present' if meta_path.is_file() else 'missing'})")
        if swagger_error:
            print(f"Schema error: {swagger_error}")
        if meta_error:
            print(f"Meta error:   {meta_error}")
        if index is not None:
            print(f"Version:    {index.swagger_version or 'unknown'}")
            print(f"Operations: {index.operation_count}")
            print(f"Endpoints:  {len(index.endpoints)}")
            print(f"Schemas:    {_field_schema_count(index)} with fields")
            print(f"Fields:     {_field_count(index)}")
        if meta:
            print(f"Fetched at: {meta.get('fetched_at', 'unknown')}")
            print(f"URL:        {meta.get('url', 'unknown')}")
            last_diff = meta.get("last_diff")
            if isinstance(last_diff, dict):
                print(f"Last drift: {_drift_summary_text(last_diff)}")
    return 0


def _run_history(args: argparse.Namespace) -> int:
    rows = _history_snapshots()
    if args.diffs:
        diff_rows = _history_diff_rows(rows)
        if args.json:
            for row in diff_rows:
                print(json.dumps(row, default=str))
        else:
            if not diff_rows:
                print("Not enough Swagger history snapshots to diff.")
            else:
                _print_history_diffs(diff_rows)
        return 0
    if args.json:
        for row in rows:
            print(json.dumps(row, default=str))
    else:
        if not rows:
            print("No Swagger history snapshots found.")
        else:
            _print_history(rows)
    return 0


def _run_endpoints(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    rows = [
        {
            "endpoint": operation.endpoint,
            "method": operation.method,
            "path": operation.path,
            "operation_id": operation.operation_id,
            "request_schema": operation.request_schema,
            "response_schema": operation.response_schema,
            "request_field_count": len(operation.request_fields),
            "response_field_count": len(operation.response_fields),
        }
        for operation in index.operations
        if not args.endpoint or operation.endpoint == args.endpoint
    ]
    if args.json:
        for row in rows:
            print(json.dumps(row, default=str))
    else:
        if not rows:
            print("No Swagger endpoints found.")
        else:
            _print_endpoints(rows)
    return 0


def _run_fields(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    rows = [
        row
        for row in _indexed_field_rows(index)
        if _matches_filters(
            row["endpoint"],
            row["method"],
            row["path"],
            args.endpoint,
            args.method,
            args.include_nested,
        )
    ]
    if args.required_only:
        rows = [row for row in rows if row["required"]]
    operations = [
        operation
        for operation in index.operations
        if _matches_filters(
            operation.endpoint,
            operation.method,
            operation.path,
            args.endpoint,
            args.method,
            args.include_nested,
        )
    ]
    if args.json:
        for row in rows:
            print(json.dumps(row, default=str))
    else:
        if not rows:
            print("No Swagger field schemas found.")
        elif args.endpoint:
            _print_field_details(rows)
        else:
            _print_field_summary(operations, required_only=args.required_only)
    return 0


def _run_field(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    rows = _indexed_field_rows(index)
    if args.endpoint:
        matches = [
            row
            for row in rows
            if row["name"] == args.selector
            and _matches_filters(
                row["endpoint"],
                row["method"],
                row["path"],
                args.endpoint,
                args.method,
                args.include_nested,
            )
        ]
        if not matches:
            raise ValueError(
                f"Swagger field not found for endpoint {args.endpoint}: {args.selector}"
            )
    else:
        if not args.selector.isdigit():
            raise ValueError(
                "Swagger field selector must be a numeric index unless --endpoint is used."
            )
        field_index = int(args.selector)
        matches = [row for row in rows if row["index"] == field_index]
        if not matches:
            raise ValueError(f"Swagger field index not found: {field_index}")
    if args.json:
        for row in matches:
            print(json.dumps(row, default=str))
    else:
        _print_field_inspection(matches)
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    comparison: dict[str, Any] | None = None
    if args.history:
        current_snapshot, previous_snapshot = _history_diff_snapshots(args.history)
        current = build_swagger_index(load_swagger_document(current_snapshot["path"]))
        previous = build_swagger_index(load_swagger_document(previous_snapshot["path"]))
        diff = diff_swagger_indexes(previous, current)
        comparison = {
            "source": "history",
            "current_index": current_snapshot["index"],
            "current_snapshot_id": current_snapshot["snapshot_id"],
            "current_path": current_snapshot["path"],
            "baseline_index": previous_snapshot["index"],
            "baseline_snapshot_id": previous_snapshot["snapshot_id"],
            "baseline_path": previous_snapshot["path"],
        }
    elif args.against:
        current = build_swagger_index(load_swagger_document())
        previous = build_swagger_index(load_swagger_document(args.against))
        diff = diff_swagger_indexes(previous, current)
    else:
        meta = load_swagger_meta()
        diff = meta.get("last_diff") if isinstance(meta, dict) else None
        if not isinstance(diff, dict):
            diff = _empty_diff()
    diff = _filter_diff(
        diff,
        endpoint=args.endpoint,
        method=args.method,
        include_nested=args.include_nested,
    )
    diff = _diff_view(
        diff,
        fields_only=args.fields_only,
        operations_only=args.operations_only,
    )
    if comparison is not None:
        diff = {**diff, "comparison": comparison}
    if args.json:
        print(json.dumps(diff, default=str))
    else:
        if comparison is not None:
            print(
                "Comparing Swagger history: "
                f"{comparison['baseline_index']} {comparison['baseline_snapshot_id']} -> "
                f"{comparison['current_index']} {comparison['current_snapshot_id']}"
            )
        _print_diff_summary(
            diff,
            fields_only=args.fields_only,
            operations_only=args.operations_only,
        )
        if not args.operations_only:
            _print_field_diff_details(diff)
        if not args.fields_only:
            _print_operation_diff_details(diff)
    return (
        1
        if _diff_count(
            diff,
            fields_only=args.fields_only,
            operations_only=args.operations_only,
        )
        else 0
    )


def _run_coverage(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    report = coverage_report(index, args.fetch_config)
    if args.json:
        print(json.dumps(report, default=str))
    else:
        _print_coverage(report)
    return 1 if report["missing_in_swagger_count"] or report["missing_in_config_count"] else 0


def _try_load_document(path: Path | None = None) -> dict[str, Any] | None:
    try:
        return load_swagger_document(path)
    except Exception:
        return None


def _try_load_status_document(path: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return load_swagger_document(path), None
    except Exception as exc:
        return None, str(exc)


def _try_load_status_meta() -> tuple[dict[str, Any] | None, str | None]:
    try:
        return load_swagger_meta(), None
    except Exception as exc:
        return None, str(exc)


def _meta_payload(
    *,
    document: dict[str, Any],
    index: Any,
    url: str,
    swagger_path: Path,
    history_path: Path,
    snapshot_id: str,
    fetched_at: str,
    diff: dict[str, Any],
) -> dict[str, Any]:
    encoded_text = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    encoded = encoded_text.encode("utf-8")
    return {
        "fetched_at": fetched_at,
        "snapshot_id": snapshot_id,
        "url": url,
        "path": str(swagger_path),
        "history_path": str(history_path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "swagger_version": index.swagger_version,
        "operation_count": index.operation_count,
        "endpoint_count": len(index.endpoints),
        "field_schema_count": _field_schema_count(index),
        "field_count": _field_count(index),
        "last_diff": diff,
    }


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_id(timestamp: str) -> str:
    return timestamp.replace(":", "").replace("-", "").replace(".", "").removesuffix("Z")


def _empty_diff() -> dict[str, Any]:
    return {
        "fields": {
            "groups": [],
            "added_count": 0,
            "removed_count": 0,
            "changed_count": 0,
        },
        "operations": {
            "added": [],
            "removed": [],
            "changed": [],
            "added_count": 0,
            "removed_count": 0,
            "changed_count": 0,
        },
        "field_added_count": 0,
        "field_removed_count": 0,
        "field_changed_count": 0,
        "operation_added_count": 0,
        "operation_removed_count": 0,
        "operation_changed_count": 0,
        "added_count": 0,
        "removed_count": 0,
        "changed_count": 0,
    }


def _print_diff_summary(
    diff: dict[str, Any],
    *,
    fields_only: bool = False,
    operations_only: bool = False,
) -> None:
    if not operations_only:
        fields = _field_diff(diff)
        print(
            "Field drift:       "
            f"+{fields.get('added_count', 0)} "
            f"-{fields.get('removed_count', 0)} "
            f"~{fields.get('changed_count', 0)}"
        )
    if not fields_only:
        operations = _operation_diff(diff)
        print(
            "Operation drift:   "
            f"+{operations.get('added_count', 0)} "
            f"-{operations.get('removed_count', 0)} "
            f"~{operations.get('changed_count', 0)}"
        )
    if not _diff_count(diff, fields_only=fields_only, operations_only=operations_only):
        print("No Swagger drift.")


def _drift_summary_text(diff: dict[str, Any]) -> str:
    fields = _field_diff(diff)
    operations = _operation_diff(diff)
    return (
        f"fields +{fields.get('added_count', 0)} -{fields.get('removed_count', 0)} "
        f"~{fields.get('changed_count', 0)}; "
        f"operations +{operations.get('added_count', 0)} "
        f"-{operations.get('removed_count', 0)} ~{operations.get('changed_count', 0)}"
    )


def _print_coverage(report: dict[str, Any]) -> None:
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


def _print_history(rows: list[dict[str, Any]]) -> None:
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


def _print_history_diffs(rows: list[dict[str, Any]]) -> None:
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


def _print_endpoints(rows: list[dict[str, Any]]) -> None:
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


def _print_field_diff_details(diff: dict[str, Any]) -> None:
    fields = _field_diff(diff)
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


def _print_operation_diff_details(diff: dict[str, Any]) -> None:
    operations = _operation_diff(diff)
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


def _diff_count(
    diff: dict[str, Any],
    *,
    fields_only: bool = False,
    operations_only: bool = False,
) -> int:
    total = 0
    if not operations_only:
        fields = _field_diff(diff)
        total += int(fields.get("added_count", 0))
        total += int(fields.get("removed_count", 0))
        total += int(fields.get("changed_count", 0))
    if not fields_only:
        operations = _operation_diff(diff)
        total += int(operations.get("added_count", 0))
        total += int(operations.get("removed_count", 0))
        total += int(operations.get("changed_count", 0))
    return total


def _indexed_field_rows(index: Any) -> list[dict[str, Any]]:
    rows = [row for operation in index.operations for row in _field_rows(operation)]
    return [{**row, "index": field_index} for field_index, row in enumerate(rows, start=1)]


def _field_rows(operation: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for surface, schema, fields in (
        ("request", operation.request_schema, operation.request_fields),
        ("response", operation.response_schema, operation.response_fields),
    ):
        for field in fields:
            rows.append(
                {
                    "endpoint": operation.endpoint,
                    "method": operation.method,
                    "path": operation.path,
                    "surface": surface,
                    "schema": schema,
                    "name": field.name,
                    "type": field.type,
                    "item_type": field.item_type,
                    "ref": field.ref,
                    "object_type": field.object_type,
                    "format": field.format,
                    "enum": field.enum,
                    "required": field.required,
                    "description": field.description,
                }
            )
    return rows


def _print_field_summary(operations: list[Any], *, required_only: bool = False) -> None:
    print("Swagger Field Schemas")
    table_rows = []
    for operation in operations:
        for surface, schema, fields in _operation_field_groups(
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


def _print_field_details(rows: list[dict[str, Any]]) -> None:
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


def _print_field_inspection(rows: list[dict[str, Any]]) -> None:
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


def _operation_field_groups(
    operation: Any,
    *,
    required_only: bool = False,
) -> list[tuple[str, str | None, tuple[Any, ...]]]:
    groups = []
    request_fields = _filter_required(operation.request_fields, required_only=required_only)
    response_fields = _filter_required(operation.response_fields, required_only=required_only)
    if request_fields:
        groups.append(("request", operation.request_schema, request_fields))
    if response_fields:
        groups.append(("response", operation.response_schema, response_fields))
    return groups


def _filter_required(fields: tuple[Any, ...], *, required_only: bool) -> tuple[Any, ...]:
    if not required_only:
        return fields
    return tuple(field for field in fields if field.required)


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


def _matches_filters(
    endpoint: str,
    method: str,
    path: str,
    endpoint_filter: str | None,
    method_filter: str,
    include_nested: bool,
) -> bool:
    if endpoint_filter and endpoint != endpoint_filter:
        return False
    if endpoint_filter and not include_nested and not _is_root_endpoint_path(path):
        return False
    return method_filter == "all" or method == method_filter.upper()


def _is_root_endpoint_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[0] in {"v1", "v2", "v3"}:
        parts = parts[1:]
    return len(parts) == 1


def _filter_diff(
    diff: dict[str, Any],
    *,
    endpoint: str | None,
    method: str,
    include_nested: bool,
) -> dict[str, Any]:
    fields = _field_diff(diff)
    operations = _operation_diff(diff)
    filtered_fields = _filter_field_diff(
        fields,
        endpoint=endpoint,
        method=method,
        include_nested=include_nested,
    )
    filtered_operations = _filter_operation_diff(
        operations,
        endpoint=endpoint,
        method=method,
        include_nested=include_nested,
    )
    return {
        "fields": filtered_fields,
        "operations": filtered_operations,
        "field_added_count": filtered_fields["added_count"],
        "field_removed_count": filtered_fields["removed_count"],
        "field_changed_count": filtered_fields["changed_count"],
        "operation_added_count": filtered_operations["added_count"],
        "operation_removed_count": filtered_operations["removed_count"],
        "operation_changed_count": filtered_operations["changed_count"],
        "added_count": filtered_fields["added_count"] + filtered_operations["added_count"],
        "removed_count": filtered_fields["removed_count"] + filtered_operations["removed_count"],
        "changed_count": filtered_fields["changed_count"] + filtered_operations["changed_count"],
    }


def _diff_view(
    diff: dict[str, Any],
    *,
    fields_only: bool,
    operations_only: bool,
) -> dict[str, Any]:
    if fields_only:
        operations = _empty_diff()["operations"]
        fields = _field_diff(diff)
    elif operations_only:
        fields = _empty_diff()["fields"]
        operations = _operation_diff(diff)
    else:
        fields = _field_diff(diff)
        operations = _operation_diff(diff)
    return {
        "fields": fields,
        "operations": operations,
        "field_added_count": fields["added_count"],
        "field_removed_count": fields["removed_count"],
        "field_changed_count": fields["changed_count"],
        "operation_added_count": operations["added_count"],
        "operation_removed_count": operations["removed_count"],
        "operation_changed_count": operations["changed_count"],
        "added_count": fields["added_count"] + operations["added_count"],
        "removed_count": fields["removed_count"] + operations["removed_count"],
        "changed_count": fields["changed_count"] + operations["changed_count"],
    }


def _filter_field_diff(
    fields: dict[str, Any],
    *,
    endpoint: str | None,
    method: str,
    include_nested: bool,
) -> dict[str, Any]:
    groups = [
        group
        for group in fields.get("groups", [])
        if _matches_filters(
            group["endpoint"],
            group["method"],
            group["path"],
            endpoint,
            method,
            include_nested,
        )
    ]
    return {
        "groups": groups,
        "added_count": sum(int(group.get("added_count", 0)) for group in groups),
        "removed_count": sum(int(group.get("removed_count", 0)) for group in groups),
        "changed_count": sum(int(group.get("changed_count", 0)) for group in groups),
    }


def _filter_operation_diff(
    operations: dict[str, Any],
    *,
    endpoint: str | None,
    method: str,
    include_nested: bool,
) -> dict[str, Any]:
    added = [
        row
        for row in operations.get("added", [])
        if _matches_filters(
            row["endpoint"], row["method"], row["path"], endpoint, method, include_nested
        )
    ]
    removed = [
        row
        for row in operations.get("removed", [])
        if _matches_filters(
            row["endpoint"], row["method"], row["path"], endpoint, method, include_nested
        )
    ]
    changed = [
        row
        for row in operations.get("changed", [])
        if _matches_filters(
            row["endpoint"], row["method"], row["path"], endpoint, method, include_nested
        )
    ]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
    }


def _field_diff(diff: dict[str, Any]) -> dict[str, Any]:
    fields = diff.get("fields")
    if isinstance(fields, dict):
        return fields
    return {"groups": [], "added_count": 0, "removed_count": 0, "changed_count": 0}


def _operation_diff(diff: dict[str, Any]) -> dict[str, Any]:
    operations = diff.get("operations")
    if isinstance(operations, dict):
        return operations
    if {"added", "removed", "changed"} <= set(diff):
        return {
            "added": diff.get("added", []),
            "removed": diff.get("removed", []),
            "changed": diff.get("changed", []),
            "added_count": int(diff.get("added_count", 0)),
            "removed_count": int(diff.get("removed_count", 0)),
            "changed_count": int(diff.get("changed_count", 0)),
        }
    return {
        "added": [],
        "removed": [],
        "changed": [],
        "added_count": 0,
        "removed_count": 0,
        "changed_count": 0,
    }


def _field_schema_count(index: Any | None) -> int:
    if index is None:
        return 0
    return len(_field_schema_groups(index))


def _field_count(index: Any | None) -> int:
    if index is None:
        return 0
    return sum(len(fields) for fields in _field_schema_groups(index).values())


def _field_schema_groups(index: Any) -> dict[str, tuple[Any, ...]]:
    groups: dict[str, tuple[Any, ...]] = {}
    for operation in index.operations:
        for surface, schema, fields in _operation_field_groups(operation):
            key = schema or f"{operation.method} {operation.path} {surface}"
            groups.setdefault(key, fields)
    return groups


def _history_diff_snapshots(indexes: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    current_index, baseline_index = indexes
    rows = _history_snapshots()
    if not rows:
        raise ValueError(f"Swagger history is empty: {resolve_swagger_history_dir()}")
    max_index = len(rows) - 1
    if current_index > max_index or baseline_index > max_index:
        raise ValueError(
            f"Swagger history index out of range. Available indexes: 0..{max_index}."
        )
    if current_index == baseline_index:
        raise ValueError("Swagger history indexes must be different.")
    return rows[current_index], rows[baseline_index]


def _history_snapshots() -> list[dict[str, Any]]:
    history_dir = resolve_swagger_history_dir()
    if not history_dir.is_dir():
        return []
    snapshot_paths = sorted(
        (
            path
            for path in history_dir.glob("*.json")
            if not path.name.endswith(".meta.json")
        ),
        key=lambda path: path.stem,
        reverse=True,
    )
    rows = [
        _history_snapshot_row(index, path)
        for index, path in enumerate(snapshot_paths)
    ]
    return rows


def _history_diff_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diff_rows = []
    for current_snapshot, baseline_snapshot in zip(rows, rows[1:], strict=False):
        current = build_swagger_index(load_swagger_document(current_snapshot["path"]))
        baseline = build_swagger_index(load_swagger_document(baseline_snapshot["path"]))
        diff = diff_swagger_indexes(baseline, current)
        diff_rows.append(
            {
                "current_index": current_snapshot["index"],
                "current_snapshot_id": current_snapshot["snapshot_id"],
                "current_path": current_snapshot["path"],
                "baseline_index": baseline_snapshot["index"],
                "baseline_snapshot_id": baseline_snapshot["snapshot_id"],
                "baseline_path": baseline_snapshot["path"],
                "field_added_count": diff["field_added_count"],
                "field_removed_count": diff["field_removed_count"],
                "field_changed_count": diff["field_changed_count"],
                "operation_added_count": diff["operation_added_count"],
                "operation_removed_count": diff["operation_removed_count"],
                "operation_changed_count": diff["operation_changed_count"],
                "added_count": diff["added_count"],
                "removed_count": diff["removed_count"],
                "changed_count": diff["changed_count"],
            }
        )
    return diff_rows


def _history_snapshot_row(index: int, path: Path) -> dict[str, Any]:
    meta_path = resolve_swagger_history_meta_path(path.stem)
    meta = _load_json_object(meta_path)
    return {
        "index": index,
        "snapshot_id": path.stem,
        "path": str(path),
        "meta_path": str(meta_path),
        "fetched_at": meta.get("fetched_at"),
        "swagger_version": meta.get("swagger_version"),
        "operation_count": meta.get("operation_count"),
        "endpoint_count": meta.get("endpoint_count"),
        "field_schema_count": meta.get("field_schema_count"),
        "field_count": meta.get("field_count"),
        "sha256": meta.get("sha256"),
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _short_sha(value: Any) -> str:
    return str(value)[:12] if value else ""


__all__ = ["run_swagger"]
