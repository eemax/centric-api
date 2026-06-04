from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, read_config_text, runtime_home, runtime_path

DEFAULT_DOWNLOAD_CONFIG_PATH = Path("config/download.yml")
PRIVATE_DOWNLOAD_CONFIG_PATH = Path("download.yml")
DEFAULT_DOWNLOAD_DIR = Path("downloads")
ROOT_CONFIG_KEYS = {"version", "output_dir", "jobs"}
JOB_CONFIG_KEYS = {"name", "sources", "document_filters", "revision_filters"}
SOURCE_CONFIG_KEYS = {"endpoint", "filters"}
FILTER_OPERATORS = {"equals", "in", "contains", "matches", "exists", "lookup"}
FILTER_CONFIG_KEYS = {"path", *FILTER_OPERATORS}
LOOKUP_CONFIG_KEYS = {"endpoint", "path", "equals", "in", "contains", "matches", "exists"}
LOOKUP_OPERATORS = {"equals", "in", "contains", "matches", "exists"}


@dataclass(frozen=True)
class DownloadLookupFilter:
    endpoint: str
    path: str
    equals: Any = None
    in_values: tuple[Any, ...] | None = None
    contains: Any = None
    matches: str | None = None
    exists: bool | None = None


@dataclass(frozen=True)
class DownloadFilter:
    path: str
    equals: Any = None
    in_values: tuple[Any, ...] | None = None
    contains: Any = None
    matches: str | None = None
    exists: bool | None = None
    lookup: DownloadLookupFilter | None = None


@dataclass(frozen=True)
class DownloadSource:
    endpoint: str
    filters: tuple[DownloadFilter, ...] = ()


@dataclass(frozen=True)
class DownloadJob:
    name: str
    sources: tuple[DownloadSource, ...]
    document_filters: tuple[DownloadFilter, ...] = ()
    revision_filters: tuple[DownloadFilter, ...] = ()


@dataclass(frozen=True)
class DownloadConfig:
    path: Path
    jobs: tuple[DownloadJob, ...]
    output_dir: Path = field(default_factory=lambda: runtime_path(DEFAULT_DOWNLOAD_DIR))


def resolve_download_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    private_path = runtime_home() / PRIVATE_DOWNLOAD_CONFIG_PATH
    if private_path.is_file():
        return private_path
    return DEFAULT_DOWNLOAD_CONFIG_PATH


def load_download_config(path: str | Path | None = None) -> DownloadConfig:
    config_path = resolve_download_config_path(path)
    payload = _load_payload(config_path)
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "download config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("download config version must be 1.")
    output_dir = _runtime_output_dir(payload.get("output_dir"))
    jobs_raw = _list(payload.get("jobs"), "jobs")
    jobs = tuple(_parse_job(raw, index) for index, raw in enumerate(jobs_raw))
    if not jobs:
        raise ConfigError("download config must contain at least one job.")
    _ensure_unique_job_names(jobs)
    return DownloadConfig(path=config_path, jobs=jobs, output_dir=output_dir)


def _load_payload(path: Path) -> dict[str, Any]:
    text = read_config_text(path, missing_message="Download config not found: {path}")
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ConfigError("Download config root must be an object.")
    return payload


def _runtime_output_dir(value: Any) -> Path:
    if value is None:
        return runtime_path(DEFAULT_DOWNLOAD_DIR)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("download output_dir must be a non-empty string.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else runtime_path(path)


def _parse_job(raw: Any, index: int) -> DownloadJob:
    if not isinstance(raw, dict):
        raise ConfigError(f"download jobs[{index}] must be an object.")
    _reject_unknown_keys(raw, JOB_CONFIG_KEYS, f"download jobs[{index}]")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"download jobs[{index}].name must be a non-empty string.")
    sources_raw = _list(raw.get("sources"), f"job[{name}].sources")
    if not sources_raw:
        raise ConfigError(f"download job[{name}].sources must be a non-empty array.")
    sources = tuple(_parse_source(item, i, name) for i, item in enumerate(sources_raw))
    document_filters = tuple(
        _parse_filter(item, f"job[{name}].document_filters[{i}]")
        for i, item in enumerate(
            _list(raw.get("document_filters", []), f"job[{name}].document_filters")
        )
    )
    revision_filters = tuple(
        _parse_filter(item, f"job[{name}].revision_filters[{i}]")
        for i, item in enumerate(
            _list(raw.get("revision_filters", []), f"job[{name}].revision_filters")
        )
    )
    return DownloadJob(
        name=name.strip(),
        sources=sources,
        document_filters=document_filters,
        revision_filters=revision_filters,
    )


def _parse_source(raw: Any, index: int, job_name: str) -> DownloadSource:
    if not isinstance(raw, dict):
        raise ConfigError(f"download job[{job_name}].sources[{index}] must be an object.")
    _reject_unknown_keys(raw, SOURCE_CONFIG_KEYS, f"download job[{job_name}].sources[{index}]")
    endpoint = raw.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ConfigError(f"download job[{job_name}].sources[{index}].endpoint is required.")
    filters = tuple(
        _parse_filter(item, f"job[{job_name}].sources[{index}].filters[{filter_index}]")
        for filter_index, item in enumerate(
            _list(raw.get("filters", []), f"job[{job_name}].sources[{index}].filters")
        )
    )
    return DownloadSource(endpoint=endpoint.strip(), filters=filters)


def _parse_filter(raw: Any, field_name: str) -> DownloadFilter:
    if not isinstance(raw, dict):
        raise ConfigError(f"download {field_name} must be an object.")
    _reject_unknown_keys(raw, FILTER_CONFIG_KEYS, f"download {field_name}")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError(f"download {field_name}.path must be a non-empty string.")
    operators = [name for name in FILTER_OPERATORS if name in raw]
    if len(operators) != 1:
        raise ConfigError(f"download {field_name} must define exactly one filter operator.")
    if "in" in raw:
        values = raw["in"]
        if not isinstance(values, list) or not values:
            raise ConfigError(f"download {field_name}.in must be a non-empty array.")
        in_values = tuple(values)
    else:
        in_values = None
    exists = raw.get("exists")
    if exists is not None and not isinstance(exists, bool):
        raise ConfigError(f"download {field_name}.exists must be true or false.")
    matches = raw.get("matches")
    if matches is not None and not isinstance(matches, str):
        raise ConfigError(f"download {field_name}.matches must be a string.")
    lookup = None
    if "lookup" in raw:
        lookup = _parse_lookup_filter(raw["lookup"], f"{field_name}.lookup")
    return DownloadFilter(
        path=path.strip(),
        equals=raw.get("equals"),
        in_values=in_values,
        contains=raw.get("contains"),
        matches=matches,
        exists=exists,
        lookup=lookup,
    )


def _parse_lookup_filter(raw: Any, field_name: str) -> DownloadLookupFilter:
    if not isinstance(raw, dict):
        raise ConfigError(f"download {field_name} must be an object.")
    _reject_unknown_keys(raw, LOOKUP_CONFIG_KEYS, f"download {field_name}")
    endpoint = raw.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ConfigError(f"download {field_name}.endpoint must be a non-empty string.")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError(f"download {field_name}.path must be a non-empty string.")
    operators = [name for name in LOOKUP_OPERATORS if name in raw]
    if len(operators) != 1:
        raise ConfigError(f"download {field_name} must define exactly one lookup operator.")
    if "in" in raw:
        values = raw["in"]
        if not isinstance(values, list) or not values:
            raise ConfigError(f"download {field_name}.in must be a non-empty array.")
        in_values = tuple(values)
    else:
        in_values = None
    exists = raw.get("exists")
    if exists is not None and not isinstance(exists, bool):
        raise ConfigError(f"download {field_name}.exists must be true or false.")
    matches = raw.get("matches")
    if matches is not None and not isinstance(matches, str):
        raise ConfigError(f"download {field_name}.matches must be a string.")
    return DownloadLookupFilter(
        endpoint=endpoint.strip(),
        path=path.strip(),
        equals=raw.get("equals"),
        in_values=in_values,
        contains=raw.get("contains"),
        matches=matches,
        exists=exists,
    )


def _list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"download {field_name} must be an array.")
    return value


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"download {field_name} has unknown keys: {', '.join(unknown)}")


def _ensure_unique_job_names(jobs: tuple[DownloadJob, ...]) -> None:
    seen: set[str] = set()
    for job in jobs:
        if job.name in seen:
            raise ConfigError(f"Duplicate download job name: {job.name}")
        seen.add(job.name)


def select_download_job(config: DownloadConfig, job_name: str | None) -> DownloadJob:
    if job_name is None:
        if len(config.jobs) == 1:
            return config.jobs[0]
        names = ", ".join(job.name for job in config.jobs)
        raise ConfigError(f"Download config has multiple jobs; pass --job. Available: {names}")
    for job in config.jobs:
        if job.name == job_name:
            return job
    names = ", ".join(job.name for job in config.jobs)
    raise ConfigError(f"Unknown download job {job_name!r}. Available: {names}")
