from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ConfigError, runtime_path
from .raw_lifecycle import (
    completed_run_dir,
    raw_run_lifecycle,
    write_completed_marker,
)
from .record_constants import (
    HARD_DELETE_TYPE_FIELD,
    MODIFIED_AT_FIELD,
    PRIMARY_KEY_FIELD,
)
from .schema import EndpointSchema

RAW_INDEX_SCHEMA_VERSION = 1
RAW_INDEX_SUFFIX = ".index.jsonl"
RAW_VERIFICATION_SCHEMA_VERSION = 1
RAW_VERIFICATION_FILE = ".verified.json"


@dataclass(frozen=True)
class RawIndexBuildResult:
    index_path: Path
    content_sha256: str
    index_sha256: str
    byte_size: int
    line_count: int
    record_count: int
    invalid_records: int


@dataclass(frozen=True)
class RawIndexRunResult:
    run_path: Path
    run_id: str
    status: str
    indexed_files: int
    skipped_files: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class RawIndexResult:
    raw_root: Path
    runs: tuple[RawIndexRunResult, ...]

    @property
    def status(self) -> str:
        return "failed" if any(run.status == "failed" for run in self.runs) else "ok"

    @property
    def indexed_files(self) -> int:
        return sum(run.indexed_files for run in self.runs)

    @property
    def skipped_files(self) -> int:
        return sum(run.skipped_files for run in self.runs)


@dataclass(frozen=True)
class RawObservation:
    endpoint: str
    record_id: str
    run_id: str
    run_started_at: str
    raw_file: Path
    index_file: Path
    line: int
    payload_sha256: str
    raw_line_sha256: str
    modified_at: str | None
    delete_type: str | None
    manifest_path: Path


@dataclass(frozen=True)
class RawCheckFile:
    endpoint: str
    file: Path
    index_file: Path | None
    status: str
    content_sha256: str | None
    expected_content_sha256: str | None
    index_sha256: str | None
    expected_index_sha256: str | None
    line_count: int | None
    expected_line_count: int | None
    byte_size: int | None
    expected_byte_size: int | None
    record_count: int | None
    expected_record_count: int | None
    invalid_records: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RawCheckResult:
    run_path: Path
    run_id: str
    status: str
    lifecycle: str
    files: tuple[RawCheckFile, ...]
    errors: int
    warnings: int


@dataclass(frozen=True)
class RawCompactResult:
    status: str
    output_dir: Path
    source_run_count: int
    source_record_count: int
    winner_count: int
    written_count: int | None
    deleted_winner_count: int | None
    archived_count: int
    dry_run: bool
    counts_exact: bool


@dataclass(frozen=True)
class RawWinnerSet:
    source_record_count: int
    winners: dict[str, dict[str, RawObservation]]


def raw_index_path(raw_path: Path) -> Path:
    if raw_path.name.endswith(".jsonl"):
        return raw_path.with_name(f"{raw_path.name[:-len('.jsonl')]}{RAW_INDEX_SUFFIX}")
    return raw_path.with_name(f"{raw_path.name}{RAW_INDEX_SUFFIX}")


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


def find_raw_observations(
    *,
    endpoint: str,
    record_id: str,
    raw_root: Path,
) -> list[RawObservation]:
    observations: list[RawObservation] = []
    for run_path in _iter_completed_run_paths(raw_root):
        manifest = load_raw_manifest(run_path)
        run_id = str(manifest.get("run_id") or run_path.name)
        run_started_at = str(manifest.get("started_at") or "")
        endpoint_record = _manifest_endpoints_dict(manifest).get(endpoint)
        if not isinstance(endpoint_record, dict):
            continue
        file_name = endpoint_record.get("file")
        index_name = endpoint_record.get("index_file")
        if not isinstance(file_name, str) or not isinstance(index_name, str):
            continue
        raw_file = run_path / file_name
        index_file = run_path / index_name
        if not index_file.is_file():
            continue
        for record in iter_raw_index(index_file):
            if str(record.get("record_id")) != record_id:
                continue
            observations.append(
                RawObservation(
                    endpoint=endpoint,
                    record_id=record_id,
                    run_id=run_id,
                    run_started_at=run_started_at,
                    raw_file=raw_file,
                    index_file=index_file,
                    line=int(record["line"]),
                    payload_sha256=str(record["payload_sha256"]),
                    raw_line_sha256=str(record["raw_line_sha256"]),
                    modified_at=_optional_string(record.get("modified_at")),
                    delete_type=_optional_string(record.get("delete_type")),
                    manifest_path=run_path / "manifest.json",
                )
            )
    return sorted(observations, key=_observation_sort_key)


def load_observation_payload(observation: RawObservation) -> dict[str, Any]:
    for line_number, raw_line in enumerate(observation.raw_file.open("rb"), start=1):
        if line_number != observation.line:
            continue
        return _parse_observation_payload(raw_line, observation, line_number)
    raise ConfigError(f"Raw observation line not found: {observation.raw_file}:{observation.line}")


def load_observation_payloads(
    observations: list[RawObservation],
) -> dict[RawObservation, dict[str, Any]]:
    grouped: dict[Path, list[RawObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.raw_file].append(observation)

    payloads: dict[RawObservation, dict[str, Any]] = {}
    for raw_file, file_observations in grouped.items():
        by_line = {observation.line: observation for observation in file_observations}
        with raw_file.open("rb") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                observation = by_line.pop(line_number, None)
                if observation is None:
                    continue
                payloads[observation] = _parse_observation_payload(
                    raw_line,
                    observation,
                    line_number,
                )
                if not by_line:
                    break
        if by_line:
            missing = min(by_line)
            raise ConfigError(f"Raw observation line not found: {raw_file}:{missing}")
    return payloads


def _parse_observation_payload(
    raw_line: bytes,
    observation: RawObservation,
    line_number: int,
) -> dict[str, Any]:
    payload = json.loads(raw_line.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError(f"Raw observation is not an object: {observation.raw_file}:{line_number}")
    return payload


def diff_payloads(left: Any, right: Any) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _diff_payloads(left, right, path="", changes=changes)
    return changes


def choose_diff_observations(
    observations: list[RawObservation],
    *,
    from_hash: str | None = None,
    to_hash: str | None = None,
) -> tuple[RawObservation, RawObservation]:
    if from_hash or to_hash:
        left = _latest_matching_hash(observations, from_hash, label="--from")
        right = _latest_matching_hash(observations, to_hash, label="--to")
        if left is None or right is None:
            raise ConfigError("Both --from and --to hashes are required for explicit raw diff.")
        return left, right
    distinct: list[RawObservation] = []
    seen: set[str] = set()
    for observation in reversed(observations):
        if observation.payload_sha256 in seen:
            continue
        seen.add(observation.payload_sha256)
        distinct.append(observation)
        if len(distinct) == 2:
            break
    if len(distinct) < 2:
        raise ConfigError("Need at least two distinct raw payload hashes to diff.")
    return distinct[1], distinct[0]


def compact_raw_runs(
    *,
    raw_root: Path,
    output_dir: Path | None,
    schemas: dict[str, EndpointSchema],
    archive_old: bool = False,
    dry_run: bool = False,
    exact_counts: bool = False,
) -> RawCompactResult:
    source_runs = _iter_completed_run_paths(raw_root)
    if not source_runs:
        raise ConfigError(f"No completed raw runs found under {raw_root / 'runs'}")
    _validate_compaction_sources(source_runs)
    winner_set = _build_winner_set(source_runs)
    return _compact_raw_runs_from_winners(
        winner_set=winner_set,
        source_runs=source_runs,
        raw_root=raw_root,
        output_dir=output_dir,
        schemas=schemas,
        archive_old=archive_old,
        dry_run=dry_run,
        exact_counts=exact_counts,
    )


def _compact_raw_runs_from_winners(
    *,
    winner_set: RawWinnerSet,
    source_runs: list[Path],
    raw_root: Path,
    output_dir: Path | None,
    schemas: dict[str, EndpointSchema],
    archive_old: bool,
    dry_run: bool,
    exact_counts: bool,
) -> RawCompactResult:
    if winner_set.source_record_count == 0:
        raise ConfigError("No raw index records found. Run a fresh fetch before compacting.")
    winner_count = _winner_count(winner_set)
    output = output_dir or completed_run_dir(raw_root, _compaction_run_id())
    source_runs_to_archive = _archiveable_source_runs(source_runs, output)
    if archive_old and not dry_run:
        _preflight_archive_targets(raw_root, source_runs_to_archive)
    deleted_count: int | None = 0
    written_count: int | None = 0
    counts_exact = not dry_run or exact_counts
    if not dry_run:
        if output.exists():
            raise ConfigError(f"Raw compaction output already exists: {output}")
        try:
            output.mkdir(parents=True)
            endpoint_records: dict[str, dict[str, Any]] = {}
            for endpoint in _winner_endpoints(winner_set):
                raw_path = output / f"{endpoint}.jsonl"
                observations = _iter_winning_observations(winner_set, endpoint=endpoint)
                payloads = load_observation_payloads(observations)
                with raw_path.open("w", encoding="utf-8") as fh:
                    for observation in observations:
                        payload = payloads[observation]
                        schema = schemas.get(endpoint, EndpointSchema(endpoint))
                        if _is_deleted_payload(payload, schema):
                            deleted_count += 1
                            continue
                        fh.write(canonical_json(payload) + "\n")
                        written_count += 1
                index_fields = raw_index_manifest_fields(raw_path, endpoint=endpoint)
                endpoint_records[endpoint] = {
                    "endpoint": endpoint,
                    "file": raw_path.name,
                    "output_file_created": True,
                    "mode": "compacted-full",
                    "status": "OK",
                    "is_delta": False,
                    "items_fetched": index_fields["record_count"],
                    "expected_count": index_fields["record_count"],
                    **index_fields,
                }
            manifest = {
                "schema_version": 2,
                "run_id": output.name,
                "mode": "compacted-full",
                "status": "OK",
                "started_at": _utc_iso(),
                "finished_at": _utc_iso(),
                "output_dir": str(output),
                "selected_endpoints": sorted(endpoint_records),
                "endpoints_total": len(endpoint_records),
                "endpoints_succeeded": len(endpoint_records),
                "endpoints_failed": 0,
                "total_items": written_count,
                "compaction": {
                    "source_runs": [path.name for path in source_runs],
                    "source_run_count": len(source_runs),
                    "source_record_count": winner_set.source_record_count,
                    "winner_count": winner_count,
                    "deleted_winner_count": deleted_count,
                },
                "endpoints": endpoint_records,
            }
            _write_json(output / "manifest.json", manifest)
            write_completed_marker(
                output,
                run_id=output.name,
                mode="compacted-full",
                started_at=str(manifest["started_at"]),
                completed_at=str(manifest["finished_at"]),
                manifest_path=output / "manifest.json",
            )
            _verify_compacted_output(output)
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise
        archived_count = (
            _archive_source_runs(raw_root, source_runs_to_archive) if archive_old else 0
        )
    elif exact_counts:
        for endpoint in _winner_endpoints(winner_set):
            observations = _iter_winning_observations(winner_set, endpoint=endpoint)
            payloads = load_observation_payloads(observations)
            for observation in observations:
                payload = payloads[observation]
                schema = schemas.get(observation.endpoint, EndpointSchema(observation.endpoint))
                if _is_deleted_payload(payload, schema):
                    deleted_count += 1
                else:
                    written_count += 1
        archived_count = 0
    else:
        written_count = None
        deleted_count = None
        archived_count = 0
    return RawCompactResult(
        status="planned" if dry_run else "ok",
        output_dir=output,
        source_run_count=len(source_runs),
        source_record_count=winner_set.source_record_count,
        winner_count=winner_count,
        written_count=written_count,
        deleted_winner_count=deleted_count,
        archived_count=archived_count,
        dry_run=dry_run,
        counts_exact=counts_exact,
    )


def iter_raw_index(index_path: Path) -> Iterator[dict[str, Any]]:
    with index_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                yield payload


def read_raw_index(index_path: Path) -> list[dict[str, Any]]:
    return list(iter_raw_index(index_path))


def load_raw_manifest(run_path: Path) -> dict[str, Any]:
    manifest_path = run_path / "manifest.json"
    if not manifest_path.is_file():
        raise ConfigError(f"Raw run manifest not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Raw run manifest is invalid JSON: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Raw run manifest must be an object: {manifest_path}")
    return payload


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


def _write_verification_seal(run_path: Path, manifest: dict[str, Any]) -> None:
    payload = {
        "schema_version": RAW_VERIFICATION_SCHEMA_VERSION,
        "run_id": str(manifest.get("run_id") or run_path.name),
        "verified_at": _utc_iso(),
        "fingerprint": _raw_run_fingerprint(run_path, manifest),
    }
    _write_json(_verification_seal_path(run_path), payload)


def _verification_seal_matches(run_path: Path, manifest: dict[str, Any]) -> bool:
    seal_path = _verification_seal_path(run_path)
    if not seal_path.is_file():
        return False
    try:
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("schema_version") != RAW_VERIFICATION_SCHEMA_VERSION:
        return False
    return payload.get("fingerprint") == _raw_run_fingerprint(run_path, manifest)


def _verification_seal_path(run_path: Path) -> Path:
    return run_path / RAW_VERIFICATION_FILE


def _raw_run_fingerprint(run_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_sha256": _sha256_path(run_path / "manifest.json"),
        "files": _raw_run_file_fingerprints(run_path, manifest),
    }


def _raw_run_file_fingerprints(run_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    for endpoint, record in _manifest_endpoint_records(manifest):
        for role, key in (("raw", "file"), ("index", "index_file")):
            value = record.get(key)
            if not isinstance(value, str) or not value:
                continue
            path = run_path / value
            try:
                stat = path.stat()
            except OSError:
                fingerprints.append(
                    {
                        "endpoint": endpoint,
                        "role": role,
                        "path": value,
                        "missing": True,
                    }
                )
                continue
            fingerprints.append(
                {
                    "endpoint": endpoint,
                    "role": role,
                    "path": value,
                    "device": stat.st_dev,
                    "inode": stat.st_ino,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "ctime_ns": stat.st_ctime_ns,
                }
            )
    return fingerprints


def resolve_raw_run_path(value: str, *, raw_root: Path | None = None) -> Path:
    raw_value = value.strip()
    if not raw_value:
        raise ConfigError("Raw run must be a run id or path.")
    explicit_path = Path(raw_value).expanduser()
    if explicit_path.exists():
        if not explicit_path.is_dir():
            raise ConfigError(f"Raw run must be a directory: {explicit_path}")
        return explicit_path.resolve()
    root = raw_root or runtime_path("raw")
    run_path = root / "runs" / raw_value
    if run_path.is_dir():
        return run_path.resolve()
    if explicit_path.is_absolute() or len(explicit_path.parts) > 1:
        raise ConfigError(f"Raw run directory not found: {explicit_path}")
    raise ConfigError(f"Raw run not found under {root / 'runs'}: {raw_value}")


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _manifest_expected_record_count(endpoint_record: dict[str, Any]) -> int | None:
    for key in ("record_count", "items_fetched", "records_written", "expected_count"):
        value = _optional_int(endpoint_record.get(key))
        if value is not None:
            return value
    return None


def _manifest_endpoint_status(endpoint_record: dict[str, Any]) -> str:
    return str(endpoint_record.get("status") or "").upper()


def _manifest_endpoint_records(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, dict):
        raise ConfigError("Raw run manifest must contain an 'endpoints' object.")
    rows: list[tuple[str, dict[str, Any]]] = []
    for endpoint, record in sorted(endpoints.items()):
        if isinstance(record, dict):
            rows.append((str(endpoint), record))
    return rows


def _manifest_endpoints_dict(manifest: dict[str, Any]) -> dict[str, Any]:
    endpoints = manifest.get("endpoints")
    return endpoints if isinstance(endpoints, dict) else {}


def _inspect_raw_file(raw_path: Path) -> tuple[str, int, int, int]:
    digest = hashlib.sha256()
    line_count = 0
    byte_size = 0
    invalid_records = 0
    with raw_path.open("rb") as fh:
        for raw_line in fh:
            digest.update(raw_line)
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
    return digest.hexdigest(), line_count, byte_size, invalid_records


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


def _validate_compaction_sources(source_runs: list[Path]) -> None:
    invalid = [
        result
        for result in (_ensure_raw_run_verified(path) for path in source_runs)
        if result.status != "ok"
    ]
    if not invalid:
        return
    details = ", ".join(f"{result.run_id}={result.status}" for result in invalid[:10])
    remaining = len(invalid) - 10
    suffix = f", ... {remaining} more" if remaining > 0 else ""
    raise ConfigError(f"Raw compaction requires verified indexed runs; found {details}{suffix}.")


def _ensure_raw_run_verified(run_path: Path) -> RawCheckResult:
    manifest = load_raw_manifest(run_path)
    if _verification_seal_matches(run_path, manifest):
        return RawCheckResult(
            run_path=run_path,
            run_id=str(manifest.get("run_id") or run_path.name),
            status="ok",
            lifecycle=raw_run_lifecycle(run_path),
            files=(),
            errors=0,
            warnings=0,
        )
    return check_raw_run(run_path)


def _build_winner_set(source_runs: list[Path]) -> RawWinnerSet:
    winners: dict[str, dict[str, RawObservation]] = defaultdict(dict)
    source_record_count = 0
    for run_path in source_runs:
        manifest = load_raw_manifest(run_path)
        run_id = str(manifest.get("run_id") or run_path.name)
        run_started_at = str(manifest.get("started_at") or "")
        for endpoint, endpoint_record in _manifest_endpoint_records(manifest):
            file_name = endpoint_record.get("file")
            index_name = endpoint_record.get("index_file")
            if not isinstance(file_name, str) or not isinstance(index_name, str):
                continue
            raw_file = run_path / file_name
            index_file = run_path / index_name
            if not index_file.is_file():
                continue
            for record in iter_raw_index(index_file):
                observation = RawObservation(
                    endpoint=endpoint,
                    record_id=str(record["record_id"]),
                    run_id=run_id,
                    run_started_at=run_started_at,
                    raw_file=raw_file,
                    index_file=index_file,
                    line=int(record["line"]),
                    payload_sha256=str(record["payload_sha256"]),
                    raw_line_sha256=str(record["raw_line_sha256"]),
                    modified_at=_optional_string(record.get("modified_at")),
                    delete_type=_optional_string(record.get("delete_type")),
                    manifest_path=run_path / "manifest.json",
                )
                source_record_count += 1
                endpoint_winners = winners[endpoint]
                current = endpoint_winners.get(observation.record_id)
                if current is None or _observation_sort_key(observation) > _observation_sort_key(
                    current
                ):
                    endpoint_winners[observation.record_id] = observation
    return RawWinnerSet(source_record_count=source_record_count, winners=dict(winners))


def _winner_count(winner_set: RawWinnerSet) -> int:
    return sum(len(records) for records in winner_set.winners.values())


def _winner_endpoints(winner_set: RawWinnerSet) -> list[str]:
    return sorted(winner_set.winners)


def _iter_winning_observations(
    winner_set: RawWinnerSet,
    *,
    endpoint: str,
) -> list[RawObservation]:
    records = winner_set.winners.get(endpoint, {})
    return [records[record_id] for record_id in sorted(records)]


def _is_deleted_payload(payload: dict[str, Any], schema: EndpointSchema) -> bool:
    if _explicit_delete_type(payload):
        return True
    for condition in schema.delete_when_any:
        if payload.get(condition.field) == condition.equals:
            return True
    return False


def _verify_compacted_output(output: Path) -> None:
    result = check_raw_run(output)
    if result.status != "ok":
        messages = [
            error
            for file in result.files
            for error in file.errors
        ]
        joined = "; ".join(messages) if messages else result.status
        raise ConfigError(f"Compacted raw run failed verification: {joined}")


def _archiveable_source_runs(source_runs: list[Path], output: Path) -> list[Path]:
    output_resolved = output.resolve()
    return [source for source in source_runs if source.resolve() != output_resolved]


def _preflight_archive_targets(raw_root: Path, source_runs: list[Path]) -> None:
    archive_root = raw_root / "archive"
    for source in source_runs:
        target = archive_root / source.name
        if target.exists():
            raise ConfigError(f"Raw archive target already exists: {target}")


def _archive_source_runs(raw_root: Path, source_runs: list[Path]) -> int:
    archive_root = raw_root / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = 0
    for source in source_runs:
        target = archive_root / source.name
        shutil.move(str(source), str(target))
        archived += 1
    return archived


def _iter_completed_run_paths(raw_root: Path) -> list[Path]:
    runs_dir = raw_root / "runs"
    if not runs_dir.is_dir():
        return []
    paths = [
        path
        for path in sorted(runs_dir.iterdir())
        if _is_compaction_source_run(path)
    ]
    return paths


def _is_compaction_source_run(path: Path) -> bool:
    if not path.is_dir() or not (path / "manifest.json").is_file():
        return False
    lifecycle = raw_run_lifecycle(path)
    if lifecycle in {"failed", "running"}:
        return False
    manifest = load_raw_manifest(path)
    status = str(manifest.get("status") or "").upper()
    return status not in {"FAILED", "PARTIAL"}


def _observation_sort_key(observation: RawObservation) -> tuple[str, str, str, int]:
    return (
        observation.modified_at or "",
        observation.run_started_at,
        observation.run_id,
        observation.line,
    )


def _latest_matching_hash(
    observations: list[RawObservation],
    payload_hash: str | None,
    *,
    label: str,
) -> RawObservation | None:
    if not payload_hash:
        return None
    matches = [item for item in observations if item.payload_sha256.startswith(payload_hash)]
    if not matches:
        raise ConfigError(f"No raw observation matched {label} hash: {payload_hash}")
    if len({item.payload_sha256 for item in matches}) > 1:
        raise ConfigError(f"{label} hash is ambiguous: {payload_hash}")
    return matches[-1]


def _diff_payloads(left: Any, right: Any, *, path: str, changes: list[dict[str, Any]]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in left:
                changes.append({"path": child_path, "from": None, "to": right[key]})
            elif key not in right:
                changes.append({"path": child_path, "from": left[key], "to": None})
            else:
                _diff_payloads(left[key], right[key], path=child_path, changes=changes)
        return
    if left != right:
        changes.append({"path": path or "$", "from": left, "to": right})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _explicit_delete_type(payload: dict[str, Any]) -> str | None:
    value = payload.get(HARD_DELETE_TYPE_FIELD)
    return str(value) if value else None


def _compaction_run_id() -> str:
    return f"{datetime.now(UTC):%Y-%m-%dT%H%M%SZ}-compacted-full"


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
