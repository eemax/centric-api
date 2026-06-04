from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import ConfigError, read_config_text, runtime_home, runtime_path

DEFAULT_VIEW_CONFIG_PATH = Path("config/views.yml")
PRIVATE_VIEW_CONFIG_PATH = Path("views.yml")
DEFAULT_EXPORT_DIR = Path("exports")
RELATIONSHIPS = {"one", "many_concat", "many_expand"}
MISSING_POLICIES = {"blank", "drop", "error"}
COLUMN_TYPES = {"text", "number", "integer", "boolean", "date", "datetime"}
FILTER_OPERATORS = {"equals", "in", "contains", "matches", "exists", "gt", "gte", "lt", "lte"}
ROOT_CONFIG_KEYS = {"version", "output_dir", "options", "views"}
VIEW_CONFIG_KEYS = {"name", "title", "root", "joins", "filters", "columns", "options"}
ROOT_VIEW_KEYS = {"endpoint", "table", "as"}
JOIN_CONFIG_KEYS = {
    "as",
    "endpoint",
    "table",
    "from",
    "to",
    "relationship",
    "missing",
    "separator",
    "filters",
}
COLUMN_CONFIG_KEYS = {"header", "path", "type", "width", "number_format"}
FILTER_CONFIG_KEYS = {"path", *FILTER_OPERATORS}
OPTIONS_CONFIG_KEYS = {
    "missing",
    "many_separator",
    "freeze_header",
    "autofilter",
    "autosize",
    "sheet_name",
}

Relationship = Literal["one", "many_concat", "many_expand"]
MissingPolicy = Literal["blank", "drop", "error"]
ColumnType = Literal["text", "number", "integer", "boolean", "date", "datetime"]


@dataclass(frozen=True)
class ViewRoot:
    endpoint: str | None
    table: str | None
    alias: str

    @property
    def source_name(self) -> str:
        return self.endpoint or self.table or ""

    @property
    def source_type(self) -> str:
        return "endpoint" if self.endpoint is not None else "table"


@dataclass(frozen=True)
class ViewJoin:
    alias: str
    endpoint: str | None
    table: str | None
    from_path: str
    to_path: str
    relationship: Relationship = "one"
    missing: MissingPolicy | None = None
    separator: str | None = None
    filters: tuple[ViewFilter, ...] = ()

    @property
    def source_name(self) -> str:
        return self.endpoint or self.table or ""

    @property
    def source_type(self) -> str:
        return "endpoint" if self.endpoint is not None else "table"


@dataclass(frozen=True)
class ViewFilter:
    path: str
    operator: str
    equals: Any = None
    in_values: tuple[Any, ...] | None = None
    contains: Any = None
    matches: str | None = None
    exists: bool | None = None
    gt: Any = None
    gte: Any = None
    lt: Any = None
    lte: Any = None


@dataclass(frozen=True)
class ViewColumn:
    header: str
    path: str
    type: ColumnType = "text"
    width: int | None = None
    number_format: str | None = None


@dataclass(frozen=True)
class ViewOptions:
    missing: MissingPolicy = "blank"
    many_separator: str = ", "
    freeze_header: bool = True
    autofilter: bool = True
    autosize: bool = True
    sheet_name: str | None = None


@dataclass(frozen=True)
class ViewDefinition:
    name: str
    title: str
    root: ViewRoot
    joins: tuple[ViewJoin, ...]
    filters: tuple[ViewFilter, ...]
    columns: tuple[ViewColumn, ...]
    options: ViewOptions = field(default_factory=ViewOptions)


@dataclass(frozen=True)
class ViewConfig:
    path: Path
    output_dir: Path
    views: tuple[ViewDefinition, ...]


def resolve_view_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    private_path = runtime_home() / PRIVATE_VIEW_CONFIG_PATH
    if private_path.is_file():
        return private_path
    return DEFAULT_VIEW_CONFIG_PATH


def load_view_config(path: str | Path | None = None) -> ViewConfig:
    config_path = resolve_view_config_path(path)
    payload = _load_payload(config_path)
    _reject_unknown_keys(payload, ROOT_CONFIG_KEYS, "view config")
    version = payload.get("version", 1)
    if version != 1:
        raise ConfigError("view config version must be 1.")
    root_options = _parse_options(payload.get("options"), "view config.options")
    output_dir = _runtime_output_dir(payload.get("output_dir"))
    views_raw = _list(payload.get("views"), "views")
    views = tuple(_parse_view(raw, index, root_options) for index, raw in enumerate(views_raw))
    if not views:
        raise ConfigError("view config must contain at least one view.")
    _ensure_unique_view_names(views)
    return ViewConfig(path=config_path, output_dir=output_dir, views=views)


def select_view(config: ViewConfig, name: str) -> ViewDefinition:
    for view in config.views:
        if view.name == name:
            return view
    names = ", ".join(view.name for view in config.views)
    raise ConfigError(f"Unknown view {name!r}. Available: {names}")


def _load_payload(path: Path) -> dict[str, Any]:
    text = read_config_text(path, missing_message="View config not found: {path}")
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ConfigError("View config root must be an object.")
    return payload


def _runtime_output_dir(value: Any) -> Path:
    if value is None:
        return runtime_path(DEFAULT_EXPORT_DIR)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("view output_dir must be a non-empty string.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else runtime_path(path)


def _parse_view(raw: Any, index: int, root_options: ViewOptions) -> ViewDefinition:
    if not isinstance(raw, dict):
        raise ConfigError(f"view views[{index}] must be an object.")
    _reject_unknown_keys(raw, VIEW_CONFIG_KEYS, f"view views[{index}]")
    name = _required_string(raw.get("name"), f"view views[{index}].name")
    title = _string_or_default(raw.get("title"), name, f"view[{name}].title")
    root = _parse_root(raw.get("root"), f"view[{name}].root")
    joins = tuple(
        _parse_join(item, f"view[{name}].joins[{join_index}]")
        for join_index, item in enumerate(_list(raw.get("joins", []), f"view[{name}].joins"))
    )
    filters = tuple(
        _parse_filter(item, f"view[{name}].filters[{filter_index}]")
        for filter_index, item in enumerate(_list(raw.get("filters", []), f"view[{name}].filters"))
    )
    columns = tuple(
        _parse_column(item, f"view[{name}].columns[{column_index}]")
        for column_index, item in enumerate(_list(raw.get("columns"), f"view[{name}].columns"))
    )
    if not columns:
        raise ConfigError(f"view[{name}] must define at least one column.")
    options = _parse_options(raw.get("options"), f"view[{name}].options", parent=root_options)
    view = ViewDefinition(
        name=name,
        title=title,
        root=root,
        joins=joins,
        filters=filters,
        columns=columns,
        options=options,
    )
    _validate_view(view)
    return view


def _parse_root(raw: Any, field_name: str) -> ViewRoot:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, ROOT_VIEW_KEYS, field_name)
    endpoint, table = _parse_source(raw, field_name)
    return ViewRoot(
        endpoint=endpoint,
        table=table,
        alias=_required_string(raw.get("as"), f"{field_name}.as"),
    )


def _parse_join(raw: Any, field_name: str) -> ViewJoin:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, JOIN_CONFIG_KEYS, field_name)
    relationship = _choice(
        raw.get("relationship", "one"),
        RELATIONSHIPS,
        f"{field_name}.relationship",
    )
    missing = None
    if "missing" in raw:
        missing = _choice(raw.get("missing"), MISSING_POLICIES, f"{field_name}.missing")
    separator = raw.get("separator")
    if separator is not None and not isinstance(separator, str):
        raise ConfigError(f"{field_name}.separator must be a string.")
    filters = tuple(
        _parse_filter(item, f"{field_name}.filters[{filter_index}]")
        for filter_index, item in enumerate(_list(raw.get("filters", []), f"{field_name}.filters"))
    )
    endpoint, table = _parse_source(raw, field_name)
    return ViewJoin(
        alias=_required_string(raw.get("as"), f"{field_name}.as"),
        endpoint=endpoint,
        table=table,
        from_path=_required_string(raw.get("from"), f"{field_name}.from"),
        to_path=_required_string(raw.get("to"), f"{field_name}.to"),
        relationship=relationship,  # type: ignore[arg-type]
        missing=missing,  # type: ignore[arg-type]
        separator=separator,
        filters=filters,
    )


def _parse_source(raw: dict[str, Any], field_name: str) -> tuple[str | None, str | None]:
    has_endpoint = raw.get("endpoint") is not None
    has_table = raw.get("table") is not None
    if has_endpoint == has_table:
        raise ConfigError(f"{field_name} must define exactly one of endpoint or table.")
    endpoint = (
        _required_string(raw.get("endpoint"), f"{field_name}.endpoint") if has_endpoint else None
    )
    table = _required_string(raw.get("table"), f"{field_name}.table") if has_table else None
    return endpoint, table


def _parse_filter(raw: Any, field_name: str) -> ViewFilter:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, FILTER_CONFIG_KEYS, field_name)
    path = _required_string(raw.get("path"), f"{field_name}.path")
    operators = [name for name in FILTER_OPERATORS if name in raw]
    if len(operators) != 1:
        raise ConfigError(f"{field_name} must define exactly one filter operator.")
    in_values = None
    if "in" in raw:
        values = raw["in"]
        if not isinstance(values, list) or not values:
            raise ConfigError(f"{field_name}.in must be a non-empty array.")
        in_values = tuple(values)
    exists = raw.get("exists")
    if exists is not None and not isinstance(exists, bool):
        raise ConfigError(f"{field_name}.exists must be true or false.")
    matches = raw.get("matches")
    if matches is not None and not isinstance(matches, str):
        raise ConfigError(f"{field_name}.matches must be a string.")
    if matches is not None:
        try:
            re.compile(matches)
        except re.error as exc:
            raise ConfigError(f"{field_name}.matches must be a valid regex: {exc}") from exc
    return ViewFilter(
        path=path,
        operator=operators[0],
        equals=raw.get("equals"),
        in_values=in_values,
        contains=raw.get("contains"),
        matches=matches,
        exists=exists,
        gt=raw.get("gt"),
        gte=raw.get("gte"),
        lt=raw.get("lt"),
        lte=raw.get("lte"),
    )


def _parse_column(raw: Any, field_name: str) -> ViewColumn:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, COLUMN_CONFIG_KEYS, field_name)
    column_type = _choice(raw.get("type", "text"), COLUMN_TYPES, f"{field_name}.type")
    width = raw.get("width")
    if width is not None and (not isinstance(width, int) or width <= 0):
        raise ConfigError(f"{field_name}.width must be a positive integer.")
    number_format = raw.get("number_format")
    if number_format is not None and not isinstance(number_format, str):
        raise ConfigError(f"{field_name}.number_format must be a string.")
    return ViewColumn(
        header=_required_string(raw.get("header"), f"{field_name}.header"),
        path=_required_string(raw.get("path"), f"{field_name}.path"),
        type=column_type,  # type: ignore[arg-type]
        width=width,
        number_format=number_format,
    )


def _parse_options(
    raw: Any,
    field_name: str,
    parent: ViewOptions | None = None,
) -> ViewOptions:
    base = parent or ViewOptions()
    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be an object.")
    _reject_unknown_keys(raw, OPTIONS_CONFIG_KEYS, field_name)
    missing = base.missing
    if "missing" in raw:
        missing = _choice(raw.get("missing"), MISSING_POLICIES, f"{field_name}.missing")
    many_separator = raw.get("many_separator", base.many_separator)
    if not isinstance(many_separator, str):
        raise ConfigError(f"{field_name}.many_separator must be a string.")
    return ViewOptions(
        missing=missing,  # type: ignore[arg-type]
        many_separator=many_separator,
        freeze_header=_bool_option(raw, "freeze_header", base.freeze_header, field_name),
        autofilter=_bool_option(raw, "autofilter", base.autofilter, field_name),
        autosize=_bool_option(raw, "autosize", base.autosize, field_name),
        sheet_name=_optional_string(raw.get("sheet_name"), f"{field_name}.sheet_name")
        if "sheet_name" in raw
        else base.sheet_name,
    )


def _validate_view(view: ViewDefinition) -> None:
    aliases = {view.root.alias}
    many_concat_aliases: set[str] = set()
    expand_branch_aliases: set[str] | None = None
    for join in view.joins:
        if join.alias in aliases:
            raise ConfigError(f"view[{view.name}] duplicates alias: {join.alias}")
        source_alias = _path_alias(join.from_path)
        if source_alias not in aliases:
            raise ConfigError(
                f"view[{view.name}] join {join.alias!r} references unknown alias {source_alias!r}."
            )
        if source_alias in many_concat_aliases:
            raise ConfigError(
                f"view[{view.name}] join {join.alias!r} cannot join from many_concat alias "
                f"{source_alias!r}."
            )
        if join.relationship == "many_expand":
            if expand_branch_aliases is not None and source_alias not in expand_branch_aliases:
                raise ConfigError(
                    f"view[{view.name}] has multiple independent many_expand joins. "
                    "Use many_concat for secondary arrays."
                )
            expand_branch_aliases = {join.alias}
        elif join.relationship == "many_concat":
            many_concat_aliases.add(join.alias)
        elif expand_branch_aliases is not None and source_alias in expand_branch_aliases:
            expand_branch_aliases.add(join.alias)
        aliases.add(join.alias)
        for item in join.filters:
            alias = _path_alias(item.path)
            if alias not in aliases:
                raise ConfigError(
                    f"view[{view.name}] join {join.alias!r} filter references unknown alias "
                    f"{alias!r}."
                )
            if alias in many_concat_aliases and alias != join.alias:
                raise ConfigError(
                    f"view[{view.name}] join {join.alias!r} filter cannot reference "
                    f"many_concat alias {alias!r}."
                )
    for item in view.filters:
        alias = _path_alias(item.path)
        if alias not in aliases:
            raise ConfigError(f"view[{view.name}] filter references unknown alias {alias!r}.")
    for column in view.columns:
        alias = _path_alias(column.path)
        if alias not in aliases:
            raise ConfigError(
                f"view[{view.name}] column {column.header!r} references unknown alias {alias!r}."
            )


def _path_alias(path: str) -> str:
    return path.split(".", 1)[0]


def _bool_option(raw: dict[str, Any], key: str, default: bool, field_name: str) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name}.{key} must be true or false.")
    return value


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _string_or_default(value: Any, default: str, field_name: str) -> str:
    if value is None:
        return default
    return _required_string(value, field_name)


def _choice(value: Any, choices: set[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ConfigError(f"{field_name} must be one of: {', '.join(sorted(choices))}.")
    return value


def _list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be an array.")
    return value


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{field_name} has unknown keys: {', '.join(unknown)}")


def _ensure_unique_view_names(views: tuple[ViewDefinition, ...]) -> None:
    seen: set[str] = set()
    for view in views:
        if view.name in seen:
            raise ConfigError(f"Duplicate view name: {view.name}")
        seen.add(view.name)
