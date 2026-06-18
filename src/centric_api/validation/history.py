from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from ..config import ConfigError, runtime_home

HistoryGroup = Literal["run", "day", "week", "month"]

DEFAULT_VALIDATION_HISTORY_DIR = Path("validation/history")
DEFAULT_VALIDATION_RUNS_DIR = Path("validation/runs")
HISTORY_SCHEMA_VERSION = 2
HISTORY_ARTIFACT_NAME = "history.json"
HISTORY_OUTPUT_JSON = "history.json"
HISTORY_OUTPUT_HTML = "history.html"
HISTORY_TEMPLATE_NAME = "validation-history.html"


@dataclass(frozen=True)
class ValidationHistoryOutput:
    group: HistoryGroup
    runs_dir: Path
    output_dir: Path
    json_path: Path
    html_path: Path
    raw_metric_count: int
    point_count: int
    run_count: int
    validators: tuple[str, ...]
    metrics: tuple[str, ...]


def build_validation_history(
    *,
    runs_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    group: HistoryGroup = "week",
    validators: tuple[str, ...] = (),
) -> ValidationHistoryOutput:
    resolved_runs_dir = (
        Path(runs_dir).expanduser() if runs_dir else runtime_home() / DEFAULT_VALIDATION_RUNS_DIR
    )
    resolved_output_dir = (
        Path(output_dir).expanduser()
        if output_dir
        else runtime_home() / DEFAULT_VALIDATION_HISTORY_DIR
    )
    if group not in {"run", "day", "week", "month"}:
        raise ConfigError("History group must be one of: run, day, week, month.")

    raw_points = _load_history_metrics(resolved_runs_dir, validators=set(validators))
    points = _group_latest_points(raw_points, group=group)
    payload = _history_payload(
        group=group,
        runs_dir=resolved_runs_dir,
        output_dir=resolved_output_dir,
        raw_points=raw_points,
        points=points,
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    json_path = resolved_output_dir / HISTORY_OUTPUT_JSON
    html_path = resolved_output_dir / HISTORY_OUTPUT_HTML
    _write_json(json_path, payload)
    _write_html(html_path, payload)
    return ValidationHistoryOutput(
        group=group,
        runs_dir=resolved_runs_dir,
        output_dir=resolved_output_dir,
        json_path=json_path,
        html_path=html_path,
        raw_metric_count=len(raw_points),
        point_count=len(points),
        run_count=_run_count(raw_points),
        validators=tuple(payload["validators"]),
        metrics=tuple(payload["metrics"]),
    )


def _load_history_metrics(runs_dir: Path, *, validators: set[str]) -> list[dict[str, Any]]:
    if not runs_dir.exists():
        return []
    points: list[dict[str, Any]] = []
    for history_path in sorted(runs_dir.glob(f"*/*/{HISTORY_ARTIFACT_NAME}")):
        payload = _load_json_object(history_path)
        if not payload:
            continue
        if _schema_version(payload) != HISTORY_SCHEMA_VERSION:
            continue
        validator = str(payload.get("validator") or history_path.parent.parent.name)
        if validators and validator not in validators:
            continue
        run_id = str(payload.get("run_id") or history_path.parent.name)
        started_at = str(payload.get("started_at") or "")
        started_dt = _parse_datetime(started_at)
        if started_dt is None:
            continue
        metrics = payload.get("metrics")
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            point = _history_point(
                metric,
                payload=payload,
                history_path=history_path,
                validator=validator,
                run_id=run_id,
                started_at=started_dt,
            )
            if point is not None:
                points.append(point)
    return sorted(points, key=lambda item: (item["started_at"], item["validator"], item["metric"]))


def _history_point(
    metric: Any,
    *,
    payload: dict[str, Any],
    history_path: Path,
    validator: str,
    run_id: str,
    started_at: datetime,
) -> dict[str, Any] | None:
    if not isinstance(metric, dict):
        return None
    metric_name = str(metric.get("metric") or "").strip()
    value = _numeric_value(metric.get("value"))
    if not metric_name or value is None:
        return None
    unit = str(metric.get("unit") or "")
    trend = str(metric.get("trend") or "")
    scope = str(metric.get("scope") or "")
    if unit not in {"percent", "count", "number"}:
        return None
    if trend not in {"up", "down", "neutral"}:
        return None
    if not scope.strip():
        return None
    brand = metric.get("brand")
    dimensions = metric.get("dimensions")
    report_path = _report_path(payload, history_path)
    return {
        "validator": validator,
        "title": payload.get("title"),
        "run_id": run_id,
        "status": payload.get("status"),
        "started_at": _isoformat(started_at),
        "finished_at": payload.get("finished_at"),
        "scope": scope,
        "brand": str(brand) if brand is not None else None,
        "metric": metric_name,
        "value": value,
        "unit": unit,
        "trend": trend,
        "numerator": _numeric_value(metric.get("numerator")),
        "denominator": _numeric_value(metric.get("denominator")),
        "dimensions": dimensions if isinstance(dimensions, dict) else {},
        "history_path": str(history_path),
        "report_path": report_path,
    }


def _report_path(payload: dict[str, Any], history_path: Path) -> str:
    report_path = payload.get("report_path")
    if isinstance(report_path, str) and report_path.strip():
        return report_path
    return str(history_path.parent / "report.xlsx")


def _group_latest_points(
    points: list[dict[str, Any]], *, group: HistoryGroup
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str | None, str, str], dict[str, Any]] = {}
    for point in points:
        started_at = _parse_datetime(str(point["started_at"]))
        if started_at is None:
            continue
        bucket_start = _bucket_start(started_at, group)
        key = (
            str(point["validator"]),
            str(point["metric"]),
            str(point["unit"]),
            str(point["scope"]),
            point.get("brand"),
            _dimension_key(point.get("dimensions")),
            _run_bucket_key(point) if group == "run" else _isoformat(bucket_start),
        )
        current = grouped.get(key)
        if current is None or str(point["started_at"]) > str(current["started_at"]):
            grouped[key] = {
                **point,
                "bucket": (
                    str(point["run_id"])
                    if group == "run"
                    else _bucket_label(bucket_start, group)
                ),
                "bucket_start": _isoformat(bucket_start),
            }
    return sorted(
        grouped.values(),
        key=lambda item: (
            item["validator"],
            item["metric"],
            item["scope"],
            item.get("brand") or "",
            _dimension_key(item.get("dimensions")),
            item["bucket_start"],
        ),
    )


def _history_payload(
    *,
    group: HistoryGroup,
    runs_dir: Path,
    output_dir: Path,
    raw_points: list[dict[str, Any]],
    points: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "generated_at": _isoformat(datetime.now(UTC)),
        "group": group,
        "runs_dir": str(runs_dir),
        "output_dir": str(output_dir),
        "run_count": _run_count(raw_points),
        "raw_metric_count": len(raw_points),
        "point_count": len(points),
        "validators": sorted({point["validator"] for point in points}),
        "metrics": sorted({point["metric"] for point in points}),
        "points": points,
        "raw_points": raw_points,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.parent / f".{path.name}.tmp"
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _dimension_key(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return json.dumps(
        {str(key): str(item) for key, item in sorted(value.items())},
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_html(path: Path, payload: dict[str, Any]) -> None:
    html = _validation_history_template()
    html_payload = _html_payload(payload)
    replacements = {
        "__GENERATED_AT__": _display_timestamp(str(payload["generated_at"])),
        "__GROUP__": str(payload["group"]),
        "__POINT_COUNT__": str(payload["point_count"]),
        "__RUN_COUNT__": str(payload["run_count"]),
        "__HISTORY_JSON__": _script_json(html_payload, sort_keys=True),
    }
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    temp_path = path.parent / f".{path.name}.tmp"
    try:
        temp_path.write_text(html, encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _html_payload(payload: dict[str, Any]) -> dict[str, Any]:
    output_dir = str(payload.get("output_dir") or "")
    points = [
        _html_point(point, output_dir=Path(output_dir) if output_dir else None)
        for point in payload.get("points", [])
        if isinstance(point, dict)
    ]
    return {
        key: value
        for key, value in payload.items()
        if key != "raw_points" and key != "points"
    } | {"points": points}


def _html_point(point: dict[str, Any], *, output_dir: Path | None) -> dict[str, Any]:
    return {
        key: _relative_artifact_path(value, output_dir=output_dir)
        if key in {"history_path", "report_path"}
        else value
        for key, value in point.items()
    }


def _relative_artifact_path(value: Any, *, output_dir: Path | None) -> Any:
    text = str(value or "")
    if not text:
        return value
    path = Path(text)
    if output_dir is None or not path.is_absolute():
        return value
    return os.path.relpath(path, output_dir)


def _validation_history_template() -> str:
    return (
        resources.files("centric_api.templates")
        .joinpath(HISTORY_TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )


def _script_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(value, sort_keys=sort_keys, default=str).replace("</", "<\\/")


def _display_timestamp(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _schema_version(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("schema_version") or 0)
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bucket_start(value: datetime, group: HistoryGroup) -> datetime:
    value = value.astimezone(UTC)
    if group == "run":
        return value
    if group == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if group == "month":
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = value - timedelta(days=value.weekday())
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_label(value: datetime, group: HistoryGroup) -> str:
    if group == "day":
        return value.strftime("%Y-%m-%d")
    if group == "month":
        return value.strftime("%Y-%m")
    year, week, _day = value.isocalendar()
    return f"{year}-W{week:02d}"


def _run_bucket_key(point: dict[str, Any]) -> str:
    return "|".join(
        (
            str(point.get("run_id") or ""),
            str(point.get("history_path") or ""),
            str(point.get("started_at") or ""),
        )
    )


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _numeric_value(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value if isfinite(float(value)) else None
    try:
        parsed = float(str(value).strip().removesuffix("%"))
    except ValueError:
        return None
    if not isfinite(parsed):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _run_count(points: list[dict[str, Any]]) -> int:
    return len({(point["validator"], point["run_id"]) for point in points})
