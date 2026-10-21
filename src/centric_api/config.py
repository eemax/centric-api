from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

CONFIG_ENV_VAR = "CENTRIC_API_CONFIG"
DEFAULT_CONFIG_FILE = Path("centric-api.yml")
DEFAULT_DATABASE = Path("centric.db")
ENDPOINT_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
API_VERSIONS = ("v2", "v3")

ROOT_KEYS = {"database", "env_file", "http", "delta", "endpoints"}
HTTP_KEYS = {"timeout", "retry_max_attempts", "retry_base_seconds", "retry_max_seconds"}
DELTA_KEYS = {"overlap_minutes"}
ENDPOINT_KEYS = {"name", "path", "api_version", "count_path", "limit", "query_params"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HttpSettings:
    timeout: float = 30.0
    retry_max_attempts: int = 3
    retry_base_seconds: float = 15.0
    retry_max_seconds: float = 30.0


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    path: str
    api_version: str
    count_path: str
    limit: int = 50
    query_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    database: Path
    env_file: Path | None
    http: HttpSettings
    overlap_minutes: int
    endpoints: tuple[EndpointSpec, ...]
    config_file: Path | None = None


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from an explicit path, $CENTRIC_API_CONFIG, or ./centric-api.yml.

    Without any config file the defaults apply: ./centric.db and the bundled endpoint list.
    Relative paths inside a config file resolve against the file's directory.
    """
    path = _resolve_config_path(config_path)
    raw: dict[str, Any] = {}
    if path is not None:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path}: config root must be a mapping.")
        raw = loaded
    _reject_unknown(raw, ROOT_KEYS, "config")
    base_dir = path.parent if path is not None else Path.cwd()

    database = _path(raw.get("database"), base_dir, "database") or (base_dir / DEFAULT_DATABASE)
    env_file = _path(raw.get("env_file"), base_dir, "env_file")
    if env_file is None and (base_dir / ".env").is_file():
        env_file = base_dir / ".env"
    if "endpoints" in raw:
        endpoints = _endpoints(raw["endpoints"], bundled=_bundled_by_name())
    else:
        endpoints = _endpoints(_default_endpoints_raw())
    return Settings(
        database=database,
        env_file=env_file,
        http=_http_settings(raw.get("http")),
        overlap_minutes=_overlap_minutes(raw.get("delta")),
        endpoints=endpoints,
        config_file=path,
    )


def default_endpoints() -> tuple[EndpointSpec, ...]:
    return _endpoints(_default_endpoints_raw())


def _resolve_config_path(config_path: str | Path | None) -> Path | None:
    if config_path is not None:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return path.resolve()
    env_value = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if env_value:
        path = Path(env_value).expanduser()
        if not path.is_file():
            raise ConfigError(f"{CONFIG_ENV_VAR} points to a missing file: {path}")
        return path.resolve()
    default = Path.cwd() / DEFAULT_CONFIG_FILE
    return default.resolve() if default.is_file() else None


def _bundled_by_name() -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in _default_endpoints_raw()}


def _default_endpoints_raw() -> Any:
    text = resources.files("centric_api").joinpath("default_endpoints.yml").read_text("utf-8")
    return yaml.safe_load(text)["endpoints"]


def _http_settings(raw: Any) -> HttpSettings:
    if raw is None:
        return HttpSettings()
    if not isinstance(raw, dict):
        raise ConfigError("http must be a mapping.")
    _reject_unknown(raw, HTTP_KEYS, "http")
    defaults = HttpSettings()
    settings = HttpSettings(
        timeout=_positive_number(raw.get("timeout", defaults.timeout), "http.timeout"),
        retry_max_attempts=_positive_int(
            raw.get("retry_max_attempts", defaults.retry_max_attempts), "http.retry_max_attempts"
        ),
        retry_base_seconds=_positive_number(
            raw.get("retry_base_seconds", defaults.retry_base_seconds), "http.retry_base_seconds"
        ),
        retry_max_seconds=_positive_number(
            raw.get("retry_max_seconds", defaults.retry_max_seconds), "http.retry_max_seconds"
        ),
    )
    if settings.retry_max_seconds < settings.retry_base_seconds:
        raise ConfigError("http.retry_max_seconds must be >= http.retry_base_seconds.")
    return settings


def _overlap_minutes(raw: Any) -> int:
    if raw is None:
        return 10
    if not isinstance(raw, dict):
        raise ConfigError("delta must be a mapping.")
    _reject_unknown(raw, DELTA_KEYS, "delta")
    value = raw.get("overlap_minutes", 10)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError("delta.overlap_minutes must be a non-negative integer.")
    return value


def _endpoints(
    raw: Any, *, bundled: dict[str, dict[str, Any]] | None = None
) -> tuple[EndpointSpec, ...]:
    """Parse an endpoint list. With ``bundled`` (a user config), an entry may also be just
    the name of a bundled endpoint (``- styles``), or a mapping without ``path`` that
    names one and overrides some keys (``- {name: bom_lines, limit: 1000}``)."""
    if not isinstance(raw, list) or not raw:
        raise ConfigError("endpoints must be a non-empty list.")
    specs: list[EndpointSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        label = f"endpoints[{index}]"
        if bundled is not None:
            item = _resolve_bundled(item, bundled, label)
        if not isinstance(item, dict):
            raise ConfigError(f"{label} must be a mapping.")
        _reject_unknown(item, ENDPOINT_KEYS, label)
        name = item.get("name")
        if not isinstance(name, str) or not ENDPOINT_NAME_RE.match(name):
            raise ConfigError(
                f"{label}.name must be lowercase snake_case (letters, digits, single underscores)."
            )
        if name in seen:
            raise ConfigError(f"Duplicate endpoint name: {name}")
        seen.add(name)
        api_version = item.get("api_version", "v2")
        if api_version not in API_VERSIONS:
            raise ConfigError(f"endpoint[{name}].api_version must be one of {API_VERSIONS}.")
        query_params = item.get("query_params") or {}
        if not isinstance(query_params, dict):
            raise ConfigError(f"endpoint[{name}].query_params must be a mapping.")
        for key in query_params:
            if str(key).split("=", 1)[0] in {"skip", "limit", "_modified_at", "sort"}:
                raise ConfigError(
                    f"endpoint[{name}].query_params must not set skip, limit, sort or "
                    "_modified_at; centric-api manages those."
                )
        specs.append(
            EndpointSpec(
                name=name,
                path=_required_path(item.get("path"), f"endpoint[{name}].path"),
                api_version=api_version,
                count_path=_required_path(item.get("count_path"), f"endpoint[{name}].count_path"),
                limit=_positive_int(item.get("limit", 50), f"endpoint[{name}].limit"),
                query_params=dict(query_params),
            )
        )
    return tuple(specs)


def _resolve_bundled(item: Any, bundled: dict[str, dict[str, Any]], label: str) -> Any:
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict) or "path" in item:
        return item  # a full definition (or an error the caller reports)
    name = item.get("name")
    if name not in bundled:
        known = ", ".join(sorted(bundled))
        raise ConfigError(
            f"{label}: {name!r} is not a bundled endpoint, so it needs path and count_path. "
            f"Bundled: {known}"
        )
    return {**bundled[name], **item}


def _path(value: Any, base_dir: Path, label: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string.")
    path = Path(value.strip()).expanduser()
    return path if path.is_absolute() else (base_dir / path)


def _required_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip().strip("/"):
        raise ConfigError(f"{label} must be a non-empty string.")
    return value.strip().strip("/")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{label} must be a positive integer.")
    return value


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{label} must be a positive number.")
    return float(value)


def _reject_unknown(raw: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise ConfigError(f"{label} has unknown keys: {', '.join(unknown)}")
