from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, runtime_home, runtime_path

DEFAULT_BUNDLE_CONFIG_PATH = Path("config/bundle.yml")
PRIVATE_BUNDLE_CONFIG_PATH = Path("bundle.yml")
DEFAULT_BUNDLE_DIR = Path("bundles")
DEFAULT_SOURCE_LABEL_FIELDS = ("node_name",)
ROOT_CONFIG_KEYS = {"version", "output_dir", "bundles"}
BUNDLE_CONFIG_KEYS = {"name", "download_job", "layout"}
LAYOUT_CONFIG_KEYS = {"source_label"}
SOURCE_LABEL_CONFIG_KEYS = {"fields", "join"}


@dataclass(frozen=True)
class SourceLabelRule:
    fields: tuple[str, ...] = DEFAULT_SOURCE_LABEL_FIELDS
    join: str = " - "


@dataclass(frozen=True)
class BundleLayout:
    source_label_rules: dict[str, SourceLabelRule] = field(
        default_factory=lambda: {"default": SourceLabelRule()}
    )


@dataclass(frozen=True)
class BundleJob:
    name: str
    download_job: str
    layout: BundleLayout = field(default_factory=BundleLayout)


@dataclass(frozen=True)
class BundleConfig:
    path: Path
    bundles: tuple[BundleJob, ...]
    output_dir: Path = field(default_factory=lambda: runtime_path(DEFAULT_BUNDLE_DIR))


def resolve_bundle_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    private_path = runtime_home() / PRIVATE_BUNDLE_CONFIG_PATH
    if private_path.is_file():
        return private_path
    return DEFAULT_BUNDLE_CONFIG_PATH


def load_bundle_config(path: str | Path | None = None) -> BundleConfig:
    config_path = resolve_bundle_config_path(path)
    payload = _load_payload(config_path)
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "bundle config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("bundle config version must be 1.")
    output_dir = _runtime_output_dir(payload.get("output_dir"))
    bundles_raw = _list(payload.get("bundles"), "bundles")
    bundles = tuple(_parse_bundle(raw, index) for index, raw in enumerate(bundles_raw))
    if not bundles:
        raise ConfigError("bundle config must contain at least one bundle.")
    _ensure_unique_bundle_names(bundles)
    return BundleConfig(path=config_path, bundles=bundles, output_dir=output_dir)


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Bundle config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError("Bundle config root must be an object.")
    return payload


def _runtime_output_dir(value: Any) -> Path:
    if value is None:
        return runtime_path(DEFAULT_BUNDLE_DIR)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("bundle output_dir must be a non-empty string.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else runtime_path(path)


def _parse_bundle(raw: Any, index: int) -> BundleJob:
    if not isinstance(raw, dict):
        raise ConfigError(f"bundle bundles[{index}] must be an object.")
    _reject_unknown_keys(raw, BUNDLE_CONFIG_KEYS, f"bundle bundles[{index}]")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"bundle bundles[{index}].name must be a non-empty string.")
    download_job = raw.get("download_job")
    if not isinstance(download_job, str) or not download_job.strip():
        raise ConfigError(f"bundle bundles[{index}].download_job must be a non-empty string.")
    return BundleJob(
        name=name.strip(),
        download_job=download_job.strip(),
        layout=_parse_layout(raw.get("layout"), f"bundle bundles[{index}].layout"),
    )


def _parse_layout(raw: Any, field_name: str) -> BundleLayout:
    if raw is None:
        return BundleLayout()
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, LAYOUT_CONFIG_KEYS, field_name)
    raw_source_labels = raw.get("source_label")
    if raw_source_labels is None:
        return BundleLayout()
    if not isinstance(raw_source_labels, dict):
        raise ConfigError(f"{field_name}.source_label must be an object.")
    rules = {"default": SourceLabelRule()}
    for endpoint, rule_raw in raw_source_labels.items():
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ConfigError(f"{field_name}.source_label keys must be non-empty strings.")
        rules[endpoint.strip()] = _parse_source_label_rule(
            rule_raw,
            f"{field_name}.source_label.{endpoint}",
        )
    if "default" not in rules:
        rules["default"] = SourceLabelRule()
    return BundleLayout(source_label_rules=rules)


def _parse_source_label_rule(raw: Any, field_name: str) -> SourceLabelRule:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, SOURCE_LABEL_CONFIG_KEYS, field_name)
    fields_raw = raw.get("fields", list(DEFAULT_SOURCE_LABEL_FIELDS))
    fields = _string_tuple(fields_raw, f"{field_name}.fields")
    join = raw.get("join", " - ")
    if not isinstance(join, str):
        raise ConfigError(f"{field_name}.join must be a string.")
    return SourceLabelRule(fields=fields, join=join)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name} must be a non-empty array.")
    fields: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{field_name}[{index}] must be a non-empty string.")
        fields.append(item.strip())
    return tuple(fields)


def _list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"bundle {field_name} must be an array.")
    return value


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{field_name} has unknown keys: {', '.join(unknown)}")


def _ensure_unique_bundle_names(bundles: tuple[BundleJob, ...]) -> None:
    seen: set[str] = set()
    for bundle in bundles:
        if bundle.name in seen:
            raise ConfigError(f"Duplicate bundle name: {bundle.name}")
        seen.add(bundle.name)


def select_bundle_job(config: BundleConfig, job_name: str | None) -> BundleJob:
    if job_name is None:
        if len(config.bundles) == 1:
            return config.bundles[0]
        names = ", ".join(bundle.name for bundle in config.bundles)
        raise ConfigError(f"Bundle config has multiple bundles; pass --job. Available: {names}")
    for bundle in config.bundles:
        if bundle.name == job_name:
            return bundle
    names = ", ".join(bundle.name for bundle in config.bundles)
    raise ConfigError(f"Unknown bundle job {job_name!r}. Available: {names}")
