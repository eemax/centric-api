from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import ConfigError, runtime_home

DEFAULT_LOAD_CONFIG_PATH = Path("config/load.yml")
PRIVATE_LOAD_CONFIG_PATH = Path("load.yml")

ROOT_CONFIG_KEYS = {"version", "jobs"}
JOB_CONFIG_KEYS = {"name", "title", "method", "path", "input", "columns", "body"}
INPUT_CONFIG_KEYS = {"header_row"}
COLUMN_CONFIG_KEYS = {"header", "headers", "type", "required", "resolve"}
RESOLVE_CONFIG_KEYS = {"endpoint", "match", "output", "filters"}
LOAD_METHODS = {"POST", "PUT"}
COLUMN_TYPES = {"text", "number", "boolean", "ref"}

ColumnType = Literal["text", "number", "boolean", "ref"]


@dataclass(frozen=True)
class LoadInput:
    header_row: int = 1


@dataclass(frozen=True)
class LoadResolve:
    endpoint: str
    match: str
    output: str = "id"
    filters: dict[str, Any] | None = None


@dataclass(frozen=True)
class LoadColumn:
    key: str
    header: str
    headers: tuple[str, ...] = ()
    type: ColumnType = "text"
    required: bool = False
    resolve: LoadResolve | None = None

    @property
    def accepted_headers(self) -> tuple[str, ...]:
        headers = [self.header, *self.headers]
        deduped: list[str] = []
        seen: set[str] = set()
        for header in headers:
            normalized = _header_key(header)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(header)
        return tuple(deduped)


@dataclass(frozen=True)
class LoadJob:
    name: str
    title: str
    method: str
    path: str
    input: LoadInput
    columns: tuple[LoadColumn, ...]
    body: dict[str, str]


@dataclass(frozen=True)
class LoadConfig:
    path: Path
    jobs: tuple[LoadJob, ...]


def load_load_config(path: str | Path | None = None) -> LoadConfig:
    if path is not None:
        config_path = Path(path).expanduser()
        return parse_load_config(_load_payload(config_path), path=config_path)

    payload = _load_payload(DEFAULT_LOAD_CONFIG_PATH)
    config_path = DEFAULT_LOAD_CONFIG_PATH
    private_path = runtime_home() / PRIVATE_LOAD_CONFIG_PATH
    if private_path.is_file():
        payload = _merge_payloads(payload, _load_payload(private_path))
        config_path = private_path
    return parse_load_config(payload, path=config_path)


def parse_load_config(payload: dict[str, Any], *, path: Path) -> LoadConfig:
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "load config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("load config version must be 1.")
    jobs_raw = payload.get("jobs")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise ConfigError("load config jobs must be a non-empty array.")
    jobs = tuple(_parse_job(raw, index) for index, raw in enumerate(jobs_raw))
    _ensure_unique_jobs(jobs)
    return LoadConfig(path=path, jobs=jobs)


def select_load_job(config: LoadConfig, name: str) -> LoadJob:
    for job in config.jobs:
        if job.name == name:
            return job
    names = ", ".join(job.name for job in config.jobs)
    raise ConfigError(f"Unknown load job {name!r}. Available: {names}")


def _parse_job(raw: Any, index: int) -> LoadJob:
    if not isinstance(raw, dict):
        raise ConfigError(f"load jobs[{index}] must be an object.")
    _reject_unknown_keys(raw, JOB_CONFIG_KEYS, f"load jobs[{index}]")
    name = _required_string(raw.get("name"), f"load jobs[{index}].name")
    method = _required_string(raw.get("method"), f"load job[{name}].method").upper()
    if method not in LOAD_METHODS:
        raise ConfigError(f"load job[{name}].method must be one of: POST, PUT.")
    path = _required_string(raw.get("path"), f"load job[{name}].path")
    if not path.startswith("/"):
        raise ConfigError(f"load job[{name}].path must start with '/'.")
    columns_raw = raw.get("columns")
    if not isinstance(columns_raw, dict) or not columns_raw:
        raise ConfigError(f"load job[{name}].columns must be a non-empty object.")
    columns = tuple(
        _parse_column(key, value, f"load job[{name}].columns[{key}]")
        for key, value in columns_raw.items()
    )
    _ensure_unique_headers(name, columns)
    body = _parse_body(raw.get("body"), name, {column.key for column in columns})
    return LoadJob(
        name=name,
        title=_string_or_default(raw.get("title"), name, f"load job[{name}].title"),
        method=method,
        path=path,
        input=_parse_input(raw.get("input"), name),
        columns=columns,
        body=body,
    )


def _parse_input(raw: Any, job_name: str) -> LoadInput:
    if raw is None:
        return LoadInput()
    if not isinstance(raw, dict):
        raise ConfigError(f"load job[{job_name}].input must be an object.")
    _reject_unknown_keys(raw, INPUT_CONFIG_KEYS, f"load job[{job_name}].input")
    header_row = raw.get("header_row", 1)
    if not isinstance(header_row, int) or header_row <= 0:
        raise ConfigError(f"load job[{job_name}].input.header_row must be a positive integer.")
    return LoadInput(header_row=header_row)


def _parse_column(key: Any, raw: Any, field_name: str) -> LoadColumn:
    column_key = _required_string(key, f"{field_name}.key")
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, COLUMN_CONFIG_KEYS, field_name)
    column_type = _choice(raw.get("type", "text"), COLUMN_TYPES, f"{field_name}.type")
    headers = raw.get("headers", [])
    if not isinstance(headers, list):
        raise ConfigError(f"{field_name}.headers must be an array.")
    resolve = _parse_resolve(raw.get("resolve"), field_name)
    if column_type == "ref" and resolve is None:
        raise ConfigError(f"{field_name}.resolve is required for ref columns.")
    if column_type != "ref" and resolve is not None:
        raise ConfigError(f"{field_name}.resolve is only valid for ref columns.")
    required = raw.get("required", False)
    if not isinstance(required, bool):
        raise ConfigError(f"{field_name}.required must be true or false.")
    return LoadColumn(
        key=column_key,
        header=_required_string(raw.get("header"), f"{field_name}.header"),
        headers=tuple(_required_string(item, f"{field_name}.headers") for item in headers),
        type=column_type,  # type: ignore[arg-type]
        required=required,
        resolve=resolve,
    )


def _parse_resolve(raw: Any, field_name: str) -> LoadResolve | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name}.resolve must be an object.")
    _reject_unknown_keys(raw, RESOLVE_CONFIG_KEYS, f"{field_name}.resolve")
    return LoadResolve(
        endpoint=_required_string(raw.get("endpoint"), f"{field_name}.resolve.endpoint"),
        match=_required_string(raw.get("match"), f"{field_name}.resolve.match"),
        output=_string_or_default(raw.get("output"), "id", f"{field_name}.resolve.output"),
        filters=_parse_resolve_filters(raw.get("filters"), field_name),
    )


def _parse_resolve_filters(raw: Any, field_name: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name}.resolve.filters must be an object.")
    filters: dict[str, Any] = {}
    for key, value in raw.items():
        filters[_required_string(key, f"{field_name}.resolve.filters key")] = value
    return filters


def _parse_body(raw: Any, job_name: str, column_keys: set[str]) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"load job[{job_name}].body must be a non-empty object.")
    body: dict[str, str] = {}
    for target, source in raw.items():
        target_key = _required_string(target, f"load job[{job_name}].body key")
        source_key = _required_string(source, f"load job[{job_name}].body[{target_key}]")
        if source_key not in column_keys:
            raise ConfigError(
                f"load job[{job_name}].body[{target_key}] references unknown column {source_key!r}."
            )
        body[target_key] = source_key
    return body


def _merge_payloads(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_keys(overlay, ROOT_CONFIG_KEYS, "load config")
    merged = dict(base)
    if "version" in overlay:
        merged["version"] = overlay["version"]
    base_jobs = base.get("jobs", [])
    overlay_jobs = overlay.get("jobs", [])
    if not isinstance(base_jobs, list) or not isinstance(overlay_jobs, list):
        raise ConfigError("load config jobs must be an array.")
    jobs_by_name: dict[str, Any] = {}
    for raw_job in [*base_jobs, *overlay_jobs]:
        if not isinstance(raw_job, dict):
            raise ConfigError("Each load job must be an object.")
        name = _required_string(raw_job.get("name"), "load job.name")
        jobs_by_name[name] = raw_job
    merged["jobs"] = list(jobs_by_name.values())
    return merged


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Load config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError("Load config root must be an object.")
    return payload


def _ensure_unique_jobs(jobs: tuple[LoadJob, ...]) -> None:
    seen: set[str] = set()
    for job in jobs:
        if job.name in seen:
            raise ConfigError(f"Duplicate load job name: {job.name}")
        seen.add(job.name)


def _ensure_unique_headers(job_name: str, columns: tuple[LoadColumn, ...]) -> None:
    seen: dict[str, str] = {}
    for column in columns:
        for header in column.accepted_headers:
            key = _header_key(header)
            existing = seen.get(key)
            if existing is not None and existing != column.key:
                raise ConfigError(
                    f"load job[{job_name}] header {header!r} is used by both "
                    f"{existing!r} and {column.key!r}."
                )
            seen[key] = column.key


def _header_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _string_or_default(value: Any, default: str, field_name: str) -> str:
    if value is None:
        return default
    return _required_string(value, field_name)


def _choice(value: Any, choices: set[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ConfigError(f"{field_name} must be one of: {', '.join(sorted(choices))}.")
    return value


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{field_name} has unknown keys: {', '.join(unknown)}.")
