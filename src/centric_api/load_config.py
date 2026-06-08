from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import ConfigError, read_config_text, runtime_home

DEFAULT_LOAD_CONFIG_PATH = Path("config/load.yml")
PRIVATE_LOAD_CONFIG_PATH = Path("load.yml")

ROOT_CONFIG_KEYS = {"version", "jobs"}
JOB_CONFIG_KEYS = {"name", "title", "method", "path", "input", "columns", "body", "workflow"}
INPUT_CONFIG_KEYS = {"header_row"}
COLUMN_CONFIG_KEYS = {"header", "headers", "type", "required", "resolve", "value_set"}
RESOLVE_CONFIG_KEYS = {"endpoint", "match", "output", "filters", "scope"}
SCOPE_CONFIG_KEYS = {"column", "endpoint", "via", "match", "output"}
VALUE_SET_CONFIG_KEYS = {"name"}
LOAD_METHODS = {"POST", "PUT"}
COLUMN_TYPES = {"text", "number", "boolean", "ref", "ref_or_id", "scoped_ref", "composition_list"}
WORKFLOWS = {"default", "style_bom", "style_supplier_quote"}

ColumnType = Literal[
    "text",
    "number",
    "boolean",
    "ref",
    "ref_or_id",
    "scoped_ref",
    "composition_list",
]
LoadSource = Literal["bundled", "private", "explicit"]
LoadWorkflow = Literal["default", "style_bom", "style_supplier_quote"]
LoadBody = dict[str, str] | str


@dataclass(frozen=True)
class LoadInput:
    header_row: int = 1


@dataclass(frozen=True)
class LoadResolve:
    endpoint: str
    match: str
    output: str = "id"
    filters: dict[str, Any] | None = None
    scope: LoadScope | None = None


@dataclass(frozen=True)
class LoadScope:
    column: str
    endpoint: str
    via: str
    match: str
    output: str = "id"


@dataclass(frozen=True)
class LoadValueSet:
    name: str


@dataclass(frozen=True)
class LoadColumn:
    key: str
    header: str
    headers: tuple[str, ...] = ()
    type: ColumnType = "text"
    required: bool = False
    resolve: LoadResolve | None = None
    value_set: LoadValueSet | None = None

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
    source: LoadSource
    source_path: Path
    method: str
    path: str
    workflow: LoadWorkflow
    input: LoadInput
    columns: tuple[LoadColumn, ...]
    body: LoadBody


@dataclass(frozen=True)
class LoadConfig:
    paths: tuple[Path, ...]
    jobs: tuple[LoadJob, ...]

    @property
    def path(self) -> Path:
        return self.paths[-1]


def load_load_config(path: str | Path | None = None) -> LoadConfig:
    if path is not None:
        config_path = Path(path).expanduser()
        return parse_load_config(_load_payload(config_path), path=config_path, source="explicit")

    bundled = parse_load_config(
        _load_payload(DEFAULT_LOAD_CONFIG_PATH),
        path=DEFAULT_LOAD_CONFIG_PATH,
        source="bundled",
    )
    private_path = runtime_home() / PRIVATE_LOAD_CONFIG_PATH
    if private_path.is_file():
        private = parse_load_config(
            _load_payload(private_path),
            path=private_path,
            source="private",
        )
        return _merge_configs(bundled, private)
    return bundled


def parse_load_config(
    payload: dict[str, Any],
    *,
    path: Path,
    source: LoadSource = "explicit",
) -> LoadConfig:
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "load config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("load config version must be 1.")
    jobs_raw = payload.get("jobs")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise ConfigError("load config jobs must be a non-empty array.")
    jobs = tuple(
        _parse_job(raw, index, source=source, source_path=path)
        for index, raw in enumerate(jobs_raw)
    )
    _ensure_unique_jobs(jobs)
    return LoadConfig(paths=(path,), jobs=jobs)


def select_load_job(config: LoadConfig, name: str) -> LoadJob:
    for job in config.jobs:
        if job.name == name:
            return job
    names = ", ".join(job.name for job in config.jobs)
    raise ConfigError(f"Unknown load job {name!r}. Available: {names}")


def _parse_job(raw: Any, index: int, *, source: LoadSource, source_path: Path) -> LoadJob:
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
    body = _parse_body(raw.get("body"), name, {column.key for column in columns}, path=path)
    return LoadJob(
        name=name,
        title=_string_or_default(raw.get("title"), name, f"load job[{name}].title"),
        source=source,
        source_path=source_path,
        method=method,
        path=path,
        workflow=_choice(raw.get("workflow", "default"), WORKFLOWS, f"load job[{name}].workflow"),  # type: ignore[arg-type]
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
    value_set = _parse_value_set(raw.get("value_set"), field_name)
    if column_type in {"ref", "ref_or_id", "scoped_ref", "composition_list"} and resolve is None:
        raise ConfigError(f"{field_name}.resolve is required for {column_type} columns.")
    if (
        column_type not in {"ref", "ref_or_id", "scoped_ref", "composition_list"}
        and resolve is not None
    ):
        raise ConfigError(
            f"{field_name}.resolve is only valid for ref, ref_or_id, scoped_ref, "
            "and composition_list columns."
        )
    if column_type == "scoped_ref" and (resolve is None or resolve.scope is None):
        raise ConfigError(f"{field_name}.resolve.scope is required for scoped_ref columns.")
    if column_type != "scoped_ref" and resolve is not None and resolve.scope is not None:
        raise ConfigError(f"{field_name}.resolve.scope is only valid for scoped_ref columns.")
    if value_set is not None and column_type != "text":
        raise ConfigError(f"{field_name}.value_set is only valid for text columns.")
    if value_set is not None and resolve is not None:
        raise ConfigError(f"{field_name}.value_set cannot be combined with resolve.")
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
        value_set=value_set,
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
        scope=_parse_scope(raw.get("scope"), field_name),
    )


def _parse_scope(raw: Any, field_name: str) -> LoadScope | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name}.resolve.scope must be an object.")
    _reject_unknown_keys(raw, SCOPE_CONFIG_KEYS, f"{field_name}.resolve.scope")
    return LoadScope(
        column=_required_string(raw.get("column"), f"{field_name}.resolve.scope.column"),
        endpoint=_required_string(raw.get("endpoint"), f"{field_name}.resolve.scope.endpoint"),
        via=_required_string(raw.get("via"), f"{field_name}.resolve.scope.via"),
        match=_required_string(raw.get("match"), f"{field_name}.resolve.scope.match"),
        output=_string_or_default(raw.get("output"), "id", f"{field_name}.resolve.scope.output"),
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


def _parse_value_set(raw: Any, field_name: str) -> LoadValueSet | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name}.value_set must be an object.")
    _reject_unknown_keys(raw, VALUE_SET_CONFIG_KEYS, f"{field_name}.value_set")
    name = _required_string(raw.get("name"), f"{field_name}.value_set.name")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ConfigError(
            f"{field_name}.value_set.name may only contain letters, numbers, dots, "
            "underscores, and hyphens."
        )
    return LoadValueSet(name=name)


def _parse_body(raw: Any, job_name: str, column_keys: set[str], *, path: str) -> LoadBody:
    _ensure_path_placeholders(job_name, path, column_keys)
    if isinstance(raw, str):
        source_key = _required_string(raw, f"load job[{job_name}].body")
        if source_key not in column_keys:
            raise ConfigError(
                f"load job[{job_name}].body references unknown column {source_key!r}."
            )
        return source_key
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"load job[{job_name}].body must be a non-empty object or column name.")
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


def _ensure_path_placeholders(job_name: str, path: str, column_keys: set[str]) -> None:
    for placeholder in _path_placeholders(path):
        if placeholder not in column_keys:
            raise ConfigError(
                f"load job[{job_name}].path references unknown column {placeholder!r}."
            )


def _path_placeholders(path: str) -> set[str]:
    return set(re.findall(r"{([A-Za-z_][A-Za-z0-9_]*)}", path))


def _merge_configs(base: LoadConfig, overlay: LoadConfig) -> LoadConfig:
    jobs_by_name = {job.name: job for job in base.jobs}
    for job in overlay.jobs:
        jobs_by_name[job.name] = job
    return LoadConfig(paths=(*base.paths, *overlay.paths), jobs=tuple(jobs_by_name.values()))


def _load_payload(path: Path) -> dict[str, Any]:
    text = read_config_text(path, missing_message="Load config not found: {path}")
    payload = yaml.safe_load(text)
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
