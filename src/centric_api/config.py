from __future__ import annotations

import json
import os
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from typing import Any

from .models import AuthSettings, CountSpec, EndpointSpec, FetcherConfig

HOME_ENV_VAR = "CENTRIC_API_HOME"
DEFAULT_HOME = Path.home() / ".centric-api"
LOCAL_ENV_CONFIG_PATH = Path("local.env")
DEFAULT_CONFIG_DIR = Path("config")
BUNDLED_DEFAULT_CONFIG_DIR = "default_config"
FETCHER_CONFIG_KEYS = {
    "timeout",
    "retry_max_attempts",
    "retry_base_seconds",
    "retry_max_seconds",
    "output_dir",
    "checkpoint_dir",
    "env_file",
    "endpoints",
}
ENDPOINT_CONFIG_KEYS = {"name", "api_version", "path", "query_params", "limit", "count_spec"}
COUNT_SPEC_CONFIG_KEYS = {"path", "query_params"}


class ConfigError(ValueError):
    pass


def _load_yaml_text(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:
        raise ConfigError("YAML config requested but PyYAML is not installed.") from exc

    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ConfigError("Config file root must be an object.")
    return payload


def _load_payload(path: Path) -> dict[str, Any]:
    text = read_config_text(path, missing_message="Config file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        payload = _load_yaml_text(text)
    else:
        raise ConfigError("Config file must be JSON or YAML.")

    if not isinstance(payload, dict):
        raise ConfigError("Config file root must be an object.")
    return payload


def _as_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be an object.")
    return value


def _as_list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be an array.")
    return value


def _as_version(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value not in {"v2", "v3"}:
        raise ConfigError(f"{field_name} must be 'v2' or 'v3'.")
    return value


def _as_path(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return value.strip().strip("/")


def _as_positive_int(value: Any, *, field_name: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer.")
    return value


def _build_count_spec(raw: dict[str, Any]) -> CountSpec:
    _reject_unknown_keys(raw, COUNT_SPEC_CONFIG_KEYS, "count_spec")
    path = _as_path(raw.get("path"), field_name="count_spec.path")
    query_params = _as_dict(raw.get("query_params"), field_name="count_spec.query_params")
    return CountSpec(
        path=path,
        query_params=query_params,
    )


def _build_endpoint_spec(raw: dict[str, Any]) -> EndpointSpec:
    _reject_unknown_keys(raw, ENDPOINT_CONFIG_KEYS, "endpoint")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("endpoint.name must be a non-empty string.")

    api_version = _as_version(raw.get("api_version"), field_name=f"endpoint[{name}].api_version")
    path = _as_path(raw.get("path"), field_name=f"endpoint[{name}].path")
    query_params = _as_dict(raw.get("query_params"), field_name=f"endpoint[{name}].query_params")

    limit = _as_positive_int(raw.get("limit", 50), field_name=f"endpoint[{name}].limit", default=50)

    count_spec_raw = raw.get("count_spec")
    if not isinstance(count_spec_raw, dict):
        raise ConfigError(f"endpoint[{name}].count_spec must be an object.")
    count_spec = _build_count_spec(count_spec_raw)

    return EndpointSpec(
        name=name.strip(),
        api_version=api_version,
        path=path,
        count_spec=count_spec,
        query_params=query_params,
        limit=limit,
    )


def _build_fetcher_config(raw: dict[str, Any]) -> FetcherConfig:
    _reject_unknown_keys(raw, FETCHER_CONFIG_KEYS | {"base_url", "auth"}, "fetcher config")
    if "base_url" in raw:
        raise ConfigError("base_url belongs in CENTRIC_BASE_URL or .env, not fetcher config.")
    if "auth" in raw:
        raise ConfigError("auth settings belong in CENTRIC_* environment variables or .env.")

    timeout = raw.get("timeout", 30.0)
    retry_max_attempts = raw.get("retry_max_attempts", 3)
    retry_base_seconds = raw.get("retry_base_seconds", 15.0)
    retry_max_seconds = raw.get("retry_max_seconds", 30.0)

    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("timeout must be a positive number.")
    if not isinstance(retry_max_attempts, int) or retry_max_attempts <= 0:
        raise ConfigError("retry_max_attempts must be a positive integer.")
    if not isinstance(retry_base_seconds, (int, float)) or retry_base_seconds <= 0:
        raise ConfigError("retry_base_seconds must be a positive number.")
    if not isinstance(retry_max_seconds, (int, float)) or retry_max_seconds <= 0:
        raise ConfigError("retry_max_seconds must be a positive number.")
    if float(retry_max_seconds) < float(retry_base_seconds):
        raise ConfigError("retry_max_seconds must be greater than or equal to retry_base_seconds.")

    output_dir = _runtime_path(raw.get("output_dir", "raw"))
    checkpoint_dir = _runtime_path(raw.get("checkpoint_dir", "checkpoints"))

    return FetcherConfig(
        timeout=float(timeout),
        retry_max_attempts=retry_max_attempts,
        retry_base_seconds=float(retry_base_seconds),
        retry_max_seconds=float(retry_max_seconds),
        output_dir=output_dir,
        checkpoint_dir=checkpoint_dir,
    )


def _build_auth_settings(raw: dict[str, Any], fetcher_cfg: FetcherConfig) -> AuthSettings:
    env_file = raw.get("env_file")
    if env_file is None:
        return AuthSettings(
            timeout=fetcher_cfg.timeout,
            env_file=resolve_private_config_path(LOCAL_ENV_CONFIG_PATH),
        )
    if not isinstance(env_file, str) or not env_file.strip():
        raise ConfigError("env_file must be a non-empty string when provided.")
    env_path = Path(env_file.strip())
    if env_path.is_absolute() or env_path.parent != Path("."):
        return AuthSettings(timeout=fetcher_cfg.timeout, env_file=env_path)
    return AuthSettings(timeout=fetcher_cfg.timeout, env_file=resolve_private_config_path(env_path))


def _ensure_unique_names(specs: Iterable[EndpointSpec]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ConfigError(f"Duplicate endpoint name: {spec.name}")
        seen.add(spec.name)


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{field_name} has unknown keys: {', '.join(unknown)}.")


def read_config_text(path: Path, *, missing_message: str) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    fallback = _default_config_text(path)
    if fallback is not None:
        return fallback
    raise ConfigError(missing_message.format(path=path))


def default_config_exists(path: Path) -> bool:
    return path.is_file() or _default_config_text(path) is not None


def _default_config_text(path: Path) -> str | None:
    if path.is_absolute() or path.parent != DEFAULT_CONFIG_DIR:
        return None
    source_path = Path(__file__).resolve().parents[2] / path
    if source_path.is_file():
        return source_path.read_text(encoding="utf-8")
    try:
        return (
            resources.files("centric_api")
            .joinpath(BUNDLED_DEFAULT_CONFIG_DIR, path.name)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        return None


def resolve_private_config_path(relative_path: str | Path, path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)

    relative = Path(relative_path)
    if relative.is_absolute():
        return relative

    return runtime_home() / relative


def runtime_home() -> Path:
    configured = os.environ.get(HOME_ENV_VAR)
    if configured and configured.strip():
        return Path(configured.strip()).expanduser()
    return DEFAULT_HOME


def runtime_path(relative_path: str | Path) -> Path:
    return _runtime_path(relative_path)


def _runtime_path(value: Any) -> Path:
    if not isinstance(value, str | Path) or not str(value).strip():
        raise ConfigError("runtime paths must be non-empty strings.")
    path = Path(str(value).strip()).expanduser()
    if path.is_absolute():
        return path
    return runtime_home() / path


def resolve_optional_private_config_path(
    relative_path: str | Path,
    path: str | Path | None = None,
) -> Path | None:
    if path is not None:
        return Path(path)

    resolved_path = resolve_private_config_path(relative_path)
    if resolved_path.is_file():
        return resolved_path
    return None


def load_fetcher_settings(
    path: str | Path,
) -> tuple[FetcherConfig, AuthSettings, list[EndpointSpec]]:
    config_path = Path(path).expanduser()
    payload = _load_payload(config_path)

    fetcher_cfg = _build_fetcher_config(payload)
    auth_settings = _build_auth_settings(payload, fetcher_cfg)

    endpoints_raw = _as_list(payload.get("endpoints"), field_name="endpoints")
    endpoints = []
    for endpoint_raw in endpoints_raw:
        if not isinstance(endpoint_raw, dict):
            raise ConfigError("Each endpoint entry must be an object.")
        endpoints.append(_build_endpoint_spec(endpoint_raw))

    if not endpoints:
        raise ConfigError("Config must contain at least one endpoint.")

    _ensure_unique_names(endpoints)
    return fetcher_cfg, auth_settings, endpoints
