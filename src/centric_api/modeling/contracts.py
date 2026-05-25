from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

ColumnType = Literal["text", "number", "integer", "boolean", "json"]
IssueSeverity = Literal["error", "warning", "info"]
ModelAction = Literal["check", "run"]
ModelStatus = Literal["ok", "attention", "failed"]


@dataclass(frozen=True)
class ModelDefinition:
    name: str
    title: str
    output_table: str
    required_endpoints: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ModelColumn:
    name: str
    type: ColumnType = "text"


@dataclass(frozen=True)
class ModelOutput:
    columns: tuple[ModelColumn, ...]
    rows: tuple[dict[str, Any], ...]
    metrics: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelIssue:
    severity: IssueSeverity
    code: str
    message: str
    endpoint: str | None = None
    record_id: str | None = None
    sample: Any = None


@dataclass(frozen=True)
class ModelRunSummary:
    run_id: str
    model_name: str
    title: str
    output_table: str
    action: ModelAction
    status: ModelStatus
    started_at: str
    finished_at: str
    row_count: int
    issue_count: int
    error_count: int
    warning_count: int
    issues: tuple[ModelIssue, ...]
    metrics: dict[str, Any] | None = None


class ModelProtocol(Protocol):
    definition: ModelDefinition

    def check(self, ctx: Any) -> None: ...

    def run(self, ctx: Any) -> ModelOutput: ...
