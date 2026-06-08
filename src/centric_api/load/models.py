from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOAD_RUNS_DIR = Path("load/runs")
LOAD_VALUE_SETS_DIR = Path("load/value-sets")
MAX_SAMPLES = 3
REVIEW_WORKBOOK_NAME = "review.xlsx"
REVIEW_COLUMN_HEADERS = (
    "_cent_load_run_id",
    "_cent_load_status",
    "_cent_load_status_code",
    "_cent_load_message",
    "_cent_load_request_path",
    "_cent_load_response_id",
    "_cent_load_processed_at",
)
RETRY_STATUSES = {"failed", "validation_error"}
REVIEW_STATUSES = {"success", "failed", "validation_error"}

LoadProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class LoadIssue:
    row: int | None
    code: str
    message: str
    column: str | None = None
    sample: Any = None


@dataclass(frozen=True)
class LoadRequest:
    row: int
    method: str
    path: str
    body: Any


@dataclass(frozen=True)
class LoadMaterialized:
    job_name: str
    title: str
    workbook_path: Path
    sheet: str
    header_row: int
    rows_scanned: int
    valid_rows: int
    error_rows: int
    issues: tuple[LoadIssue, ...]
    requests: tuple[LoadRequest, ...]


@dataclass(frozen=True)
class LoadResponse:
    row: int
    status_code: int
    ok: bool
    body: Any


@dataclass(frozen=True)
class LoadRunResult:
    run_id: str
    job_name: str
    title: str
    mode: str
    dry_run: bool
    workbook_path: Path
    sheet: str
    rows_scanned: int
    valid_rows: int
    error_rows: int
    request_count: int
    success_count: int
    failure_count: int
    issues: tuple[LoadIssue, ...]
    requests: tuple[LoadRequest, ...]
    responses: tuple[LoadResponse, ...]
    run_dir: Path
    review_path: Path | None
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class StyleBomRow:
    row: int
    values: dict[str, Any]


@dataclass(frozen=True)
class StyleSupplierQuoteRow:
    row: int
    values: dict[str, Any]


@dataclass(frozen=True)
class StyleSupplierQuoteReferences:
    suppliers: tuple[dict[str, Any], ...]
    factories: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class LoadValueSetIndex:
    name: str
    path: Path
    values: tuple[str, ...]
    exact: dict[str, str]
    normalized: dict[str, str]
    loose: dict[str, str]
