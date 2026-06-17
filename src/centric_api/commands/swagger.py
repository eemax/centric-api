from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from ..auth import AuthContext, resolve_credentials
from ..config import load_fetcher_settings
from ..rendering.swagger import (
    drift_summary_text,
    print_coverage,
    print_diff_summary,
    print_endpoints,
    print_field_details,
    print_field_diff_details,
    print_field_inspection,
    print_field_summary,
    print_history,
    print_history_diffs,
    print_operation_diff_details,
)
from ..swagger import (
    build_swagger_index,
    coverage_report,
    diff_swagger_indexes,
    load_swagger_document,
    load_swagger_meta,
    resolve_swagger_history_meta_path,
    resolve_swagger_history_path,
    resolve_swagger_meta_path,
    resolve_swagger_path,
    write_swagger_document,
    write_swagger_meta,
)
from ..swagger.history import (
    build_snapshot_id,
    history_diff_rows,
    history_diff_snapshots,
    history_snapshots,
    utc_timestamp,
)
from ..swagger.inspection import (
    diff_count,
    diff_view,
    empty_diff,
    field_count,
    field_schema_count,
    filter_diff,
    indexed_field_rows,
    matches_filters,
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
    diff = empty_diff()
    if previous_document is not None:
        diff = diff_swagger_indexes(build_swagger_index(previous_document), current_index)

    fetched_at = utc_timestamp()
    snapshot_id = build_snapshot_id(fetched_at)
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
        print_diff_summary(diff)
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
        "field_schema_count": field_schema_count(index),
        "field_count": field_count(index),
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
            print(f"Schemas:    {field_schema_count(index)} with fields")
            print(f"Fields:     {field_count(index)}")
        if meta:
            print(f"Fetched at: {meta.get('fetched_at', 'unknown')}")
            print(f"URL:        {meta.get('url', 'unknown')}")
            last_diff = meta.get("last_diff")
            if isinstance(last_diff, dict):
                print(f"Last drift: {drift_summary_text(last_diff)}")
    return 0


def _run_history(args: argparse.Namespace) -> int:
    rows = history_snapshots()
    if args.diffs:
        diff_rows = history_diff_rows(rows)
        if args.json:
            for row in diff_rows:
                print(json.dumps(row, default=str))
        else:
            if not diff_rows:
                print("Not enough Swagger history snapshots to diff.")
            else:
                print_history_diffs(diff_rows)
        return 0
    if args.json:
        for row in rows:
            print(json.dumps(row, default=str))
    else:
        if not rows:
            print("No Swagger history snapshots found.")
        else:
            print_history(rows)
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
            print_endpoints(rows)
    return 0


def _run_fields(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    rows = [
        row
        for row in indexed_field_rows(index)
        if matches_filters(
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
        if matches_filters(
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
            print_field_details(rows)
        else:
            print_field_summary(operations, required_only=args.required_only)
    return 0


def _run_field(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    rows = indexed_field_rows(index)
    if args.endpoint:
        matches = [
            row
            for row in rows
            if row["name"] == args.selector
            and matches_filters(
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
        print_field_inspection(matches)
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    comparison: dict[str, Any] | None = None
    if args.history:
        current_snapshot, previous_snapshot = history_diff_snapshots(args.history)
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
            diff = empty_diff()
    diff = filter_diff(
        diff,
        endpoint=args.endpoint,
        method=args.method,
        include_nested=args.include_nested,
    )
    diff = diff_view(
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
        print_diff_summary(
            diff,
            fields_only=args.fields_only,
            operations_only=args.operations_only,
        )
        if not args.operations_only:
            print_field_diff_details(diff)
        if not args.fields_only:
            print_operation_diff_details(diff)
    return (
        1
        if diff_count(
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
        print_coverage(report)
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
        "field_schema_count": field_schema_count(index),
        "field_count": field_count(index),
        "last_diff": diff,
    }


__all__ = ["run_swagger"]
