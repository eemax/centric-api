from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import view_writers as _view_writers
from .config import ConfigError
from .view_config import ViewConfig, ViewDefinition
from .view_materialize import MissingJoinDetail, ViewMaterialized, materialize_view

ExportFormat = Literal["xlsx", "csv"]
SUPPORTED_EXPORT_FORMATS = {"xlsx", "csv"}

# Keep public class identity and the csv monkeypatch hook compatible with the old facade.
MissingJoinDetail.__module__ = __name__
ViewMaterialized.__module__ = __name__
csv = _view_writers.csv

_write_csv = _view_writers._write_csv
_write_xlsx = _view_writers._write_xlsx
_default_output_path = _view_writers._default_output_path

__all__ = [
    "ExportFormat",
    "MissingJoinDetail",
    "SUPPORTED_EXPORT_FORMATS",
    "ViewCheckResult",
    "ViewExportResult",
    "ViewMaterialized",
    "check_view",
    "csv",
    "export_view",
    "infer_export_format",
    "materialize_view",
]


@dataclass(frozen=True)
class ViewExportResult:
    view_name: str
    title: str
    format: str
    output_path: Path
    row_count: int
    column_count: int
    missing_join_count: int
    missing_join_details: tuple[MissingJoinDetail, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ViewCheckResult:
    view_name: str
    title: str
    root_row_count: int
    row_count: int
    column_count: int
    missing_join_count: int
    missing_join_details: tuple[MissingJoinDetail, ...]
    warnings: tuple[str, ...]


def check_view(db_path: Path, view: ViewDefinition) -> ViewCheckResult:
    materialized = materialize_view(db_path, view)
    return ViewCheckResult(
        view_name=view.name,
        title=view.title,
        root_row_count=materialized.root_row_count,
        row_count=len(materialized.rows),
        column_count=len(materialized.headers),
        missing_join_count=materialized.missing_join_count,
        missing_join_details=materialized.missing_join_details,
        warnings=materialized.warnings,
    )


def export_view(
    db_path: Path,
    config: ViewConfig,
    view: ViewDefinition,
    *,
    export_format: str = "xlsx",
    output_path: Path | None = None,
) -> ViewExportResult:
    if export_format not in SUPPORTED_EXPORT_FORMATS:
        raise ConfigError(
            f"view export format must be one of: {', '.join(sorted(SUPPORTED_EXPORT_FORMATS))}."
        )
    resolved_output_path = output_path or _default_output_path(
        config.output_dir, view, export_format
    )
    materialized = materialize_view(db_path, view)
    if export_format == "csv":
        _write_csv(resolved_output_path, materialized)
    else:
        _write_xlsx(resolved_output_path, materialized, view)
    return ViewExportResult(
        view_name=view.name,
        title=view.title,
        format=export_format,
        output_path=resolved_output_path,
        row_count=len(materialized.rows),
        column_count=len(materialized.headers),
        missing_join_count=materialized.missing_join_count,
        missing_join_details=materialized.missing_join_details,
        warnings=materialized.warnings,
    )


def infer_export_format(output_path: Path | None, requested_format: str | None) -> str:
    if requested_format is not None:
        if requested_format not in SUPPORTED_EXPORT_FORMATS:
            raise ConfigError(
                f"view export format must be one of: {', '.join(sorted(SUPPORTED_EXPORT_FORMATS))}."
            )
        if output_path is not None:
            suffix = output_path.suffix.lower().lstrip(".")
            if suffix in SUPPORTED_EXPORT_FORMATS and suffix != requested_format:
                raise ConfigError("view export --format must match --output extension.")
        return requested_format
    if output_path is not None:
        suffix = output_path.suffix.lower().lstrip(".")
        if suffix in SUPPORTED_EXPORT_FORMATS:
            return suffix
        if suffix:
            raise ConfigError("view export output extension must be .xlsx or .csv.")
    return "xlsx"
