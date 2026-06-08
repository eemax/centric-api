from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..config import ConfigError
from ..load_config import LoadJob
from .models import LoadIssue
from .utils import _is_blank, _lookup_key


def _map_headers(
    job: LoadJob,
    worksheet: Any,
) -> tuple[dict[str, int], list[LoadIssue], dict[str, int]]:
    header_cells = next(
        worksheet.iter_rows(
            min_row=job.input.header_row,
            max_row=job.input.header_row,
            values_only=True,
        ),
        (),
    )
    actual_headers: dict[str, list[tuple[int, str]]] = {}
    for index, value in enumerate(header_cells):
        if value is None or str(value).strip() == "":
            continue
        text = str(value).strip()
        actual_headers.setdefault(_lookup_key(text), []).append((index, text))
    mapping: dict[str, int] = {}
    aliases = 0
    issues: list[LoadIssue] = []
    for column in job.columns:
        matches: list[tuple[int, str]] = []
        for accepted in column.accepted_headers:
            matches.extend(actual_headers.get(_lookup_key(accepted), []))
        unique_matches = sorted(set(matches))
        if len(unique_matches) > 1:
            issues.append(
                LoadIssue(
                    row=None,
                    code="ambiguous_header",
                    column=column.key,
                    message=(
                        f"Column {column.key!r} matched multiple headers: "
                        + ", ".join(header for _index, header in unique_matches)
                    ),
                )
            )
        elif not unique_matches and column.required:
            issues.append(
                LoadIssue(
                    row=None,
                    code="missing_required_header",
                    column=column.key,
                    message=f"Missing required header: {column.header}",
                )
            )
        elif unique_matches:
            index, header = unique_matches[0]
            mapping[column.key] = index
            if _lookup_key(header) != _lookup_key(column.header):
                aliases += 1
    stats = {
        "matched": len(mapping),
        "required_matched": sum(
            1 for column in job.columns if column.required and column.key in mapping
        ),
        "required": sum(1 for column in job.columns if column.required),
        "aliases": aliases,
    }
    return mapping, issues, stats


def _retry_status_index(
    worksheet: Any,
    header_row: int,
    *,
    retry_statuses: set[str] | None,
) -> int | None:
    if retry_statuses is None:
        return None
    header_cells = next(
        worksheet.iter_rows(min_row=header_row, max_row=header_row, values_only=True),
        (),
    )
    for index, value in enumerate(header_cells):
        if _lookup_key(str(value or "")) == _lookup_key("_cent_load_status"):
            return index
    raise ConfigError("Retry workbook is missing _cent_load_status.")


def _include_retry_row(
    row_values: tuple[Any, ...],
    retry_status_index: int | None,
    retry_statuses: set[str] | None,
) -> bool:
    if retry_statuses is None:
        return True
    value = _cell_value(row_values, retry_status_index)
    return _lookup_key(str(value or "")) in retry_statuses


def _select_sheet(workbook: Any, sheet: str | None) -> Any:
    if sheet is None:
        return workbook.worksheets[0]
    if sheet not in workbook.sheetnames:
        names = ", ".join(workbook.sheetnames)
        raise ConfigError(f"Workbook sheet {sheet!r} not found. Available: {names}")
    return workbook[sheet]


def _iter_data_rows(worksheet: Any, header_row: int) -> Iterator[tuple[int, tuple[Any, ...]]]:
    for row_number, values in enumerate(
        worksheet.iter_rows(min_row=header_row + 1, values_only=True),
        start=header_row + 1,
    ):
        row_values = tuple(values)
        if all(_is_blank(value) for value in row_values):
            continue
        yield row_number, row_values


def _cell_value(row_values: tuple[Any, ...], index: int | None) -> Any:
    if index is None or index >= len(row_values):
        return None
    return row_values[index]
