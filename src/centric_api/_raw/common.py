from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import ConfigError, runtime_path
from ..raw_lifecycle import raw_run_lifecycle
from ..record_constants import HARD_DELETE_TYPE_FIELD, PRIMARY_KEY_FIELD

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
        return raw_path.with_name(f"{raw_path.name[: -len('.jsonl')]}{RAW_INDEX_SUFFIX}")
    return raw_path.with_name(f"{raw_path.name}{RAW_INDEX_SUFFIX}")


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


def _verification_seal_path(run_path: Path) -> Path:
    return run_path / RAW_VERIFICATION_FILE


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


def _iter_completed_run_paths(raw_root: Path) -> list[Path]:
    runs_dir = raw_root / "runs"
    if not runs_dir.is_dir():
        return []
    paths = [path for path in sorted(runs_dir.iterdir()) if _is_compaction_source_run(path)]
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


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
