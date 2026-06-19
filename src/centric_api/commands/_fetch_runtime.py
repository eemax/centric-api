from __future__ import annotations

import argparse
import calendar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..delta import apply_data_sort, strip_modified_at_filters
from ..models import EndpointSpec, FetchRunResult
from ..raw_lifecycle import active_run_dir, failed_run_dir
from ..record_constants import MODIFIED_AT_FIELD
from .common import utc_iso


def resolve_fetch_mode(args: argparse.Namespace, now: datetime) -> tuple[str, str | None]:
    if args.full and (args.days is not None or args.months is not None):
        raise ConfigError("Use --full, --days, or --months separately.")
    if args.days is not None and args.months is not None:
        raise ConfigError("Use either --days or --months, not both.")
    if args.delta_dry_run:
        return "delta", None
    if args.full:
        return "full", None
    if args.days is not None:
        return "days", utc_iso(now - timedelta(days=args.days))
    if args.months is not None:
        return "months", utc_iso(_subtract_calendar_months(now, args.months))
    return "delta", None


def prepare_runtime_spec(
    spec: EndpointSpec,
    *,
    mode: str,
    delta_floor: str | None,
    modified_since: str | None,
) -> EndpointSpec:
    runtime_spec = apply_data_sort(spec, sort_value=MODIFIED_AT_FIELD, policy="force")
    if mode == "delta" and delta_floor is not None:
        return _apply_modified_since_filter(runtime_spec, delta_floor)
    if mode in {"days", "months"} and modified_since is not None:
        return _apply_modified_since_filter(runtime_spec, modified_since)
    return runtime_spec


def select_endpoints(all_specs: list[EndpointSpec], names: list[str]) -> list[EndpointSpec]:
    if not names:
        return all_specs
    wanted = set(names)
    selected = [spec for spec in all_specs if spec.name in wanted]
    missing = sorted(wanted - {spec.name for spec in selected})
    if missing:
        raise ConfigError(f"Unknown endpoint names: {', '.join(missing)}")
    return selected


def allocate_run_id(
    raw_root_dir: Path,
    value: datetime,
    mode: str,
    amount: int | None,
) -> str:
    base = _run_id(value, mode, amount)
    runs_dir = raw_root_dir / "runs"
    for index in range(100):
        suffix = "" if index == 0 else f"-{index + 1}"
        run_id = f"{base}{suffix}"
        if (
            active_run_dir(raw_root_dir, run_id).exists()
            or (runs_dir / run_id).exists()
            or failed_run_dir(raw_root_dir, run_id).exists()
        ):
            continue
        return run_id
    raise RuntimeError("Could not allocate fetch run id.")


def remap_result_output_files(results: list[FetchRunResult], run_dir: Path) -> None:
    for result in results:
        if result.output_file_created:
            result.output_file = run_dir / result.output_file.name


def delta_floor_reason(
    delta_state: dict[str, Any],
    endpoint_name: str,
    delta_state_exists: bool,
) -> str:
    if not delta_state_exists:
        return "delta_state_missing"
    endpoints = delta_state.get("endpoints")
    if not isinstance(endpoints, dict) or endpoint_name not in endpoints:
        return "endpoint_not_tracked"
    endpoint_state = endpoints.get(endpoint_name)
    if not isinstance(endpoint_state, dict):
        return "endpoint_state_invalid"
    if not endpoint_state.get("last_successful_fetch_start"):
        return "no_successful_fetch_start"
    return "invalid_successful_fetch_start"


def endpoint_window_context(
    *,
    mode: str,
    delta_floor: str | None,
    delta_floor_reason: str | None,
    modified_since: str | None,
) -> str | None:
    if mode == "delta":
        if delta_floor is not None:
            return f"delta_floor={delta_floor}"
        return f"delta_floor=none reason={delta_floor_reason or 'unknown'}"
    if modified_since is not None:
        return f"modified_since={modified_since}"
    return None


def _apply_modified_since_filter(spec: EndpointSpec, modified_since: str) -> EndpointSpec:
    query_params = strip_modified_at_filters(spec.query_params)
    query_params[f"{MODIFIED_AT_FIELD}=ge"] = modified_since
    count_query_params = strip_modified_at_filters(spec.count_spec.query_params)
    count_query_params[f"{MODIFIED_AT_FIELD}=ge"] = modified_since
    next_count_spec = replace(spec.count_spec, query_params=count_query_params)
    return replace(spec, query_params=query_params, count_spec=next_count_spec)


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    total_month_index = (value.year * 12 + (value.month - 1)) - months
    year = total_month_index // 12
    month = (total_month_index % 12) + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _run_id(value: datetime, mode: str, amount: int | None) -> str:
    base = value.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    if mode in {"days", "months"} and amount is not None:
        return f"{base}-{mode}{amount}"
    return f"{base}-{mode}"
