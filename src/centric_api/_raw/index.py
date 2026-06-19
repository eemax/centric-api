from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..record_constants import MODIFIED_AT_FIELD, PRIMARY_KEY_FIELD
from .common import (
    RAW_INDEX_SCHEMA_VERSION,
    RawIndexBuildResult,
    RawIndexResult,
    RawIndexRunResult,
    _explicit_delete_type,
    _inspect_raw_file,
    _iter_completed_run_paths,
    _manifest_endpoint_records,
    _manifest_endpoint_status,
    _manifest_expected_record_count,
    _optional_int,
    _optional_string,
    _verification_seal_path,
    _write_json,
    canonical_json,
    load_raw_manifest,
    raw_index_path,
    resolve_raw_run_path,
    sha256_text,
)


def build_raw_index(
    raw_path: Path,
    *,
    endpoint: str,
    index_path: Path | None = None,
) -> RawIndexBuildResult:
    resolved_index_path = index_path or raw_index_path(raw_path)
    content_digest = hashlib.sha256()
    index_digest = hashlib.sha256()
    byte_size = 0
    line_count = 0
    record_count = 0
    invalid_records = 0
    temp_path = resolved_index_path.with_name(f".{resolved_index_path.name}.tmp")
    resolved_index_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with raw_path.open("rb") as raw_fh, temp_path.open("wb") as index_fh:
            for line_number, raw_line in enumerate(raw_fh, start=1):
                content_digest.update(raw_line)
                byte_size += len(raw_line)
                text = raw_line.decode("utf-8").strip()
                if not text:
                    continue
                line_count += 1
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    invalid_records += 1
                    continue
                if not isinstance(payload, dict) or payload.get(PRIMARY_KEY_FIELD) is None:
                    invalid_records += 1
                    continue
                canonical_payload = canonical_json(payload)
                record = {
                    "schema_version": RAW_INDEX_SCHEMA_VERSION,
                    "endpoint": endpoint,
                    "record_id": str(payload[PRIMARY_KEY_FIELD]),
                    "line": line_number,
                    "payload_sha256": sha256_text(canonical_payload),
                    "raw_line_sha256": hashlib.sha256(raw_line).hexdigest(),
                    "modified_at": _optional_string(payload.get(MODIFIED_AT_FIELD)),
                    "delete_type": _explicit_delete_type(payload),
                }
                index_line = (
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                index_fh.write(index_line)
                index_digest.update(index_line)
                record_count += 1
        temp_path.replace(resolved_index_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return RawIndexBuildResult(
        index_path=resolved_index_path,
        content_sha256=content_digest.hexdigest(),
        index_sha256=index_digest.hexdigest(),
        byte_size=byte_size,
        line_count=line_count,
        record_count=record_count,
        invalid_records=invalid_records,
    )


def raw_index_manifest_fields(raw_path: Path, *, endpoint: str) -> dict[str, Any]:
    result = build_raw_index(raw_path, endpoint=endpoint)
    return {
        "index_file": result.index_path.name,
        "content_sha256": result.content_sha256,
        "index_sha256": result.index_sha256,
        "byte_size": result.byte_size,
        "line_count": result.line_count,
        "record_count": result.record_count,
        "index_schema_version": RAW_INDEX_SCHEMA_VERSION,
    }


def index_raw_runs(
    *,
    raw_root: Path,
    raw_run: str | None = None,
    all_runs: bool = False,
) -> RawIndexResult:
    if bool(raw_run) == bool(all_runs):
        raise ConfigError("Provide exactly one of RAW_RUN or --all for raw index.")
    run_paths = (
        tuple(_iter_completed_run_paths(raw_root))
        if all_runs
        else (resolve_raw_run_path(str(raw_run), raw_root=raw_root),)
    )
    if not run_paths:
        raise ConfigError(f"No trusted raw runs found under {raw_root / 'runs'}")
    runs = tuple(_index_raw_run(path) for path in run_paths)
    return RawIndexResult(raw_root=raw_root, runs=runs)


def _index_raw_run(run_path: Path) -> RawIndexRunResult:
    manifest = load_raw_manifest(run_path)
    errors: list[str] = []
    skipped_files = 0
    candidates: list[tuple[str, dict[str, Any], Path]] = []
    for endpoint, endpoint_record in _manifest_endpoint_records(manifest):
        file_name = endpoint_record.get("file")
        expected_count = _manifest_expected_record_count(endpoint_record)
        if not isinstance(file_name, str) or not file_name:
            if expected_count == 0 and _manifest_endpoint_status(endpoint_record) == "OK":
                skipped_files += 1
                continue
            errors.append(f"{endpoint}: missing raw file name")
            continue
        raw_path = run_path / file_name
        if not raw_path.is_file():
            errors.append(f"{endpoint}: raw file missing ({file_name})")
            continue
        index_name = endpoint_record.get("index_file")
        index_path = run_path / index_name if isinstance(index_name, str) and index_name else None
        if index_path is not None and index_path.is_file():
            skipped_files += 1
            continue
        errors.extend(_raw_index_preflight_errors(raw_path, endpoint, endpoint_record))
        candidates.append((endpoint, endpoint_record, raw_path))
    indexed_files = 0
    if errors:
        _verification_seal_path(run_path).unlink(missing_ok=True)
        return RawIndexRunResult(
            run_path=run_path,
            run_id=str(manifest.get("run_id") or run_path.name),
            status="failed",
            indexed_files=0,
            skipped_files=skipped_files,
            errors=tuple(errors),
        )
    for endpoint, endpoint_record, raw_path in candidates:
        index_fields = raw_index_manifest_fields(raw_path, endpoint=endpoint)
        endpoint_record.update(index_fields)
        indexed_files += 1
    if indexed_files:
        manifest["schema_version"] = max(_optional_int(manifest.get("schema_version")) or 0, 2)
        _write_json(run_path / "manifest.json", manifest)
        _verification_seal_path(run_path).unlink(missing_ok=True)
    return RawIndexRunResult(
        run_path=run_path,
        run_id=str(manifest.get("run_id") or run_path.name),
        status="ok",
        indexed_files=indexed_files,
        skipped_files=skipped_files,
        errors=(),
    )


def _raw_index_preflight_errors(
    raw_path: Path,
    endpoint: str,
    endpoint_record: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    content_sha, line_count, byte_size, invalid_records = _inspect_raw_file(raw_path)
    if invalid_records:
        errors.append(f"{endpoint}: raw file has invalid records")
    expected_content_sha = _optional_string(endpoint_record.get("content_sha256"))
    if expected_content_sha and content_sha != expected_content_sha:
        errors.append(f"{endpoint}: raw file hash mismatch")
    expected_line_count = _optional_int(endpoint_record.get("line_count"))
    if expected_line_count is not None and line_count != expected_line_count:
        errors.append(f"{endpoint}: raw line count mismatch")
    expected_byte_size = _optional_int(endpoint_record.get("byte_size"))
    if expected_byte_size is not None and byte_size != expected_byte_size:
        errors.append(f"{endpoint}: raw byte size mismatch")
    expected_record_count = _manifest_expected_record_count(endpoint_record)
    if expected_record_count is not None and line_count != expected_record_count:
        errors.append(f"{endpoint}: raw record count mismatch")
    return errors
