from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..raw_lifecycle import raw_run_lifecycle
from ..record_constants import PRIMARY_KEY_FIELD
from .common import (
    RAW_VERIFICATION_SCHEMA_VERSION,
    RawCheckFile,
    RawCheckResult,
    _inspect_raw_file,
    _manifest_endpoint_records,
    _manifest_endpoint_status,
    _manifest_expected_record_count,
    _optional_int,
    _optional_string,
    _raw_run_fingerprint,
    _sha256_path,
    _utc_iso,
    _verification_seal_path,
    _write_json,
    canonical_json,
    load_raw_manifest,
    read_raw_index,
    resolve_raw_run_path,
    sha256_text,
)


def check_raw_run(run_path: Path) -> RawCheckResult:
    manifest = load_raw_manifest(run_path)
    files = tuple(
        _check_manifest_file(run_path, endpoint, endpoint_record)
        for endpoint, endpoint_record in _manifest_endpoint_records(manifest)
    )
    error_count = sum(len(item.errors) for item in files)
    warning_count = sum(len(item.warnings) for item in files)
    result = RawCheckResult(
        run_path=run_path,
        run_id=str(manifest.get("run_id") or run_path.name),
        status="failed" if error_count else ("warn" if warning_count else "ok"),
        lifecycle=raw_run_lifecycle(run_path),
        files=files,
        errors=error_count,
        warnings=warning_count,
    )
    if result.status == "ok":
        _write_verification_seal(run_path, manifest)
    else:
        _verification_seal_path(run_path).unlink(missing_ok=True)
    return result


def check_raw_runs(raw_root: Path, raw_run: str | None = None) -> tuple[RawCheckResult, ...]:
    if raw_run:
        return (check_raw_run(resolve_raw_run_path(raw_run, raw_root=raw_root)),)
    runs_dir = raw_root / "runs"
    if not runs_dir.is_dir():
        return ()
    return tuple(
        check_raw_run(path)
        for path in sorted(runs_dir.iterdir())
        if path.is_dir() and (path / "manifest.json").is_file()
    )


def _check_manifest_file(
    run_path: Path,
    endpoint: str,
    endpoint_record: dict[str, Any],
) -> RawCheckFile:
    errors: list[str] = []
    warnings: list[str] = []
    file_name = endpoint_record.get("file")
    expected_record_count = _manifest_expected_record_count(endpoint_record)
    if not isinstance(file_name, str) or not file_name.strip():
        if expected_record_count == 0 and _manifest_endpoint_status(endpoint_record) == "OK":
            return RawCheckFile(
                endpoint=endpoint,
                file=run_path,
                index_file=None,
                status="ok",
                content_sha256=None,
                expected_content_sha256=None,
                index_sha256=None,
                expected_index_sha256=None,
                line_count=0,
                expected_line_count=_optional_int(endpoint_record.get("line_count")),
                byte_size=0,
                expected_byte_size=_optional_int(endpoint_record.get("byte_size")),
                record_count=0,
                expected_record_count=expected_record_count,
                invalid_records=0,
                errors=(),
                warnings=(),
            )
        return RawCheckFile(
            endpoint=endpoint,
            file=run_path,
            index_file=None,
            status="failed",
            content_sha256=None,
            expected_content_sha256=None,
            index_sha256=None,
            expected_index_sha256=None,
            line_count=None,
            expected_line_count=None,
            byte_size=None,
            expected_byte_size=None,
            record_count=None,
            expected_record_count=None,
            invalid_records=0,
            errors=("missing file name",),
            warnings=(),
        )
    raw_path = run_path / file_name
    expected_content_sha = _optional_string(endpoint_record.get("content_sha256"))
    expected_line_count = _optional_int(endpoint_record.get("line_count"))
    expected_byte_size = _optional_int(endpoint_record.get("byte_size"))
    content_sha = None
    line_count = None
    byte_size = None
    invalid_records = 0
    if not raw_path.is_file():
        errors.append("raw file missing")
    else:
        content_sha, line_count, byte_size, invalid_records = _inspect_raw_file(raw_path)
        if expected_content_sha and content_sha != expected_content_sha:
            errors.append("raw file hash mismatch")
        if expected_line_count is not None and line_count != expected_line_count:
            errors.append("raw line count mismatch")
        if expected_byte_size is not None and byte_size != expected_byte_size:
            errors.append("raw byte size mismatch")
        if invalid_records:
            errors.append("raw file has invalid records")
    index_name = endpoint_record.get("index_file")
    index_path = run_path / index_name if isinstance(index_name, str) and index_name else None
    index_sha = None
    record_count = None
    expected_index_sha = _optional_string(endpoint_record.get("index_sha256"))
    if index_path is None:
        warnings.append("raw index missing from legacy manifest")
    elif not index_path.is_file():
        errors.append("raw index file missing")
    else:
        index_sha = _sha256_path(index_path)
        if expected_index_sha and index_sha != expected_index_sha:
            errors.append("raw index hash mismatch")
        try:
            index_records = read_raw_index(index_path)
            record_count = len(index_records)
        except (OSError, json.JSONDecodeError):
            index_records = []
            errors.append("raw index is invalid JSONL")
        if (
            expected_record_count is not None
            and record_count is not None
            and record_count != expected_record_count
        ):
            errors.append("raw index record count mismatch")
        if line_count is not None and record_count is not None and line_count != record_count:
            errors.append("raw/index record count mismatch")
        if raw_path.is_file() and index_records:
            errors.extend(_validate_index_records(raw_path, endpoint, index_records))
    return RawCheckFile(
        endpoint=endpoint,
        file=raw_path,
        index_file=index_path,
        status="failed" if errors else ("warn" if warnings else "ok"),
        content_sha256=content_sha,
        expected_content_sha256=expected_content_sha,
        index_sha256=index_sha,
        expected_index_sha256=expected_index_sha,
        line_count=line_count,
        expected_line_count=expected_line_count,
        byte_size=byte_size,
        expected_byte_size=expected_byte_size,
        record_count=record_count,
        expected_record_count=expected_record_count,
        invalid_records=invalid_records,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _write_verification_seal(run_path: Path, manifest: dict[str, Any]) -> None:
    payload = {
        "schema_version": RAW_VERIFICATION_SCHEMA_VERSION,
        "run_id": str(manifest.get("run_id") or run_path.name),
        "verified_at": _utc_iso(),
        "fingerprint": _raw_run_fingerprint(run_path, manifest),
    }
    _write_json(_verification_seal_path(run_path), payload)


def _validate_index_records(
    raw_path: Path,
    endpoint: str,
    index_records: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    records_by_line: dict[int, dict[str, Any]] = {}
    for record in index_records:
        line_number = _optional_int(record.get("line"))
        if line_number is None:
            errors.append("raw index record missing line")
            continue
        if line_number in records_by_line:
            errors.append(f"raw index has duplicate line {line_number}")
            continue
        records_by_line[line_number] = record
    with raw_path.open("rb") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            record = records_by_line.pop(line_number, None)
            if record is None:
                continue
            errors.extend(_validate_index_record(raw_line, endpoint, record, line_number))
    for line_number in sorted(records_by_line):
        errors.append(f"raw index points to missing line {line_number}")
    return errors


def _validate_index_record(
    raw_line: bytes,
    endpoint: str,
    record: dict[str, Any],
    line_number: int,
) -> list[str]:
    errors: list[str] = []
    expected_raw_hash = _optional_string(record.get("raw_line_sha256"))
    actual_raw_hash = hashlib.sha256(raw_line).hexdigest()
    if expected_raw_hash and expected_raw_hash != actual_raw_hash:
        errors.append(f"raw line hash mismatch at line {line_number}")
    try:
        payload = json.loads(raw_line.decode("utf-8"))
    except json.JSONDecodeError:
        errors.append(f"raw index points to invalid JSON at line {line_number}")
        return errors
    if not isinstance(payload, dict):
        errors.append(f"raw index points to non-object JSON at line {line_number}")
        return errors
    if str(record.get("endpoint")) != endpoint:
        errors.append(f"raw index endpoint mismatch at line {line_number}")
    if str(record.get("record_id")) != str(payload.get(PRIMARY_KEY_FIELD)):
        errors.append(f"raw index record id mismatch at line {line_number}")
    expected_payload_hash = _optional_string(record.get("payload_sha256"))
    actual_payload_hash = sha256_text(canonical_json(payload))
    if expected_payload_hash and expected_payload_hash != actual_payload_hash:
        errors.append(f"raw payload hash mismatch at line {line_number}")
    return errors
