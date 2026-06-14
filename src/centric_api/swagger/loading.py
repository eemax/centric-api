from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ConfigError, runtime_path

DEFAULT_SWAGGER_DIR = Path("swagger")
DEFAULT_SWAGGER_PATH = DEFAULT_SWAGGER_DIR / "current.json"
DEFAULT_SWAGGER_META_PATH = DEFAULT_SWAGGER_DIR / "current.meta.json"
DEFAULT_SWAGGER_HISTORY_DIR = DEFAULT_SWAGGER_DIR / "history"


def resolve_swagger_path() -> Path:
    return runtime_path(DEFAULT_SWAGGER_PATH)


def resolve_swagger_meta_path() -> Path:
    return runtime_path(DEFAULT_SWAGGER_META_PATH)


def resolve_swagger_history_dir() -> Path:
    return runtime_path(DEFAULT_SWAGGER_HISTORY_DIR)


def resolve_swagger_history_path(snapshot_id: str) -> Path:
    return resolve_swagger_history_dir() / f"{snapshot_id}.json"


def resolve_swagger_history_meta_path(snapshot_id: str) -> Path:
    return resolve_swagger_history_dir() / f"{snapshot_id}.meta.json"


def load_swagger_document(path: str | Path | None = None) -> dict[str, Any]:
    resolved_path = Path(path).expanduser() if path is not None else resolve_swagger_path()
    if not resolved_path.is_file():
        raise ConfigError(f"Swagger file not found: {resolved_path}")
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Swagger file is not valid JSON: {resolved_path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("Swagger file root must be an object.")
    return payload


def load_swagger_meta() -> dict[str, Any] | None:
    resolved_path = resolve_swagger_meta_path()
    if not resolved_path.is_file():
        return None
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Swagger metadata is not valid JSON: {resolved_path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("Swagger metadata root must be an object.")
    return payload


def write_swagger_document(path: Path, payload: dict[str, Any]) -> None:
    _write_json_atomic(path, payload)


def write_swagger_meta(path: Path, payload: dict[str, Any]) -> None:
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
