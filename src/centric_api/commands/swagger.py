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
    if args.action == "endpoints":
        return _run_endpoints(args)
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
    previous_document = _try_load_document(swagger_path)

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

    write_swagger_document(swagger_path, document)
    meta = _meta_payload(
        document=document,
        index=current_index,
        url=swagger_url,
        swagger_path=swagger_path,
        diff=diff,
    )
    write_swagger_meta(meta_path, meta)
    if args.json:
        print(json.dumps({"swagger_path": str(swagger_path), "meta_path": str(meta_path), **meta}))
    else:
        print(f"Refreshed Swagger: {swagger_path}")
        print(f"Metadata:          {meta_path}")
        print(f"Operations:        {current_index.operation_count}")
        print(f"Endpoints:         {len(current_index.endpoints)}")
        _print_diff_summary(diff)
    return 0


def _run_status(args: argparse.Namespace) -> int:
    swagger_path = resolve_swagger_path()
    meta_path = resolve_swagger_meta_path()
    document, swagger_error = _try_load_status_document(swagger_path)
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
        if meta:
            print(f"Fetched at: {meta.get('fetched_at', 'unknown')}")
            print(f"URL:        {meta.get('url', 'unknown')}")
    return 0


def _run_endpoints(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    rows = [
        {
            "endpoint": operation.endpoint,
            "method": operation.method,
            "path": operation.path,
            "operation_id": operation.operation_id,
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
        for row in rows:
            print(f"{row['method']:<6} {row['path']} ({row['endpoint']})")
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    current = build_swagger_index(load_swagger_document())
    if args.against:
        previous = build_swagger_index(load_swagger_document(args.against))
        diff = diff_swagger_indexes(previous, current)
    else:
        meta = load_swagger_meta()
        diff = meta.get("last_diff") if isinstance(meta, dict) else None
        if not isinstance(diff, dict):
            diff = _empty_diff()
    if args.json:
        print(json.dumps(diff, default=str))
    else:
        _print_diff_summary(diff)
        _print_diff_details(diff)
    return 1 if _diff_count(diff) else 0


def _run_coverage(args: argparse.Namespace) -> int:
    index = build_swagger_index(load_swagger_document())
    report = coverage_report(index, args.fetch_config)
    if args.json:
        print(json.dumps(report, default=str))
    else:
        print("Swagger Coverage")
        print(f"Configured endpoints:      {report['configured_count']}")
        print(f"Swagger GET collections:   {report['swagger_get_collection_count']}")
        print(f"Missing in Swagger:        {report['missing_in_swagger_count']}")
        print(f"Missing in fetch config:   {report['missing_in_config_count']}")
        for row in report["missing_in_swagger"]:
            print(f"- config only: {row['name']} -> {row['path']}")
        for row in report["missing_in_config"]:
            print(f"- swagger only: {row['path']}")
    return 1 if report["missing_in_swagger_count"] or report["missing_in_config_count"] else 0


def _try_load_document(path: Path) -> dict[str, Any] | None:
    try:
        return load_swagger_document(path)
    except Exception:
        return None


def _try_load_status_document(path: Path) -> tuple[dict[str, Any] | None, str | None]:
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
        "fetched_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "url": url,
        "path": str(swagger_path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "swagger_version": index.swagger_version,
        "operation_count": index.operation_count,
        "endpoint_count": len(index.endpoints),
        "last_diff": diff,
    }


def _empty_diff() -> dict[str, Any]:
    return {
        "added": [],
        "removed": [],
        "changed": [],
        "added_count": 0,
        "removed_count": 0,
        "changed_count": 0,
    }


def _print_diff_summary(diff: dict[str, Any]) -> None:
    print(
        "Drift:             "
        f"+{diff.get('added_count', 0)} "
        f"-{diff.get('removed_count', 0)} "
        f"~{diff.get('changed_count', 0)}"
    )


def _print_diff_details(diff: dict[str, Any]) -> None:
    for label, key in (("added", "added"), ("removed", "removed"), ("changed", "changed")):
        for row in diff.get(key, []):
            print(f"- {label}: {row['method']} {row['path']}")


def _diff_count(diff: dict[str, Any]) -> int:
    return int(diff.get("added_count", 0)) + int(diff.get("removed_count", 0)) + int(
        diff.get("changed_count", 0)
    )


__all__ = ["run_swagger"]
