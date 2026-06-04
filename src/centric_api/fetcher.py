from __future__ import annotations

import json
import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .auth import AuthContext
from .delta import build_delta_endpoint_spec
from .fetch_checkpoint import (
    count_file_lines,
    read_checkpoint,
    seed_resume_window_id_state,
    track_item_id,
    truncate_file_lines,
    write_checkpoint,
)
from .fetch_common import (
    ApiLogCallback,
    FetchError,
    _emit_api_log,
    _emit_progress,
    _safe_name,
)
from .fetch_pagination import get_expected_count, iter_pages
from .models import EndpointSpec, FetcherConfig, FetchProgressEvent, FetchRunResult


def run_endpoint(
    spec: EndpointSpec,
    auth_ctx: AuthContext,
    fetcher_cfg: FetcherConfig,
    resume: bool = False,
    append_output: bool = False,
    output_file_suffix: str = "",
    create_empty_output: bool = True,
    delta_floor: str | None = None,
    modified_since: str | None = None,
    progress_callback: Callable[[FetchProgressEvent], None] | None = None,
    api_log_callback: ApiLogCallback = None,
    resume_completed_hint: bool | None = None,
) -> FetchRunResult:
    started = time.time()
    retries_used_ref = [0]
    warnings: list[str] = []

    safe_name = _safe_name(spec.name)
    output_path = fetcher_cfg.output_dir / f"{safe_name}{output_file_suffix}.jsonl"
    checkpoint_path = fetcher_cfg.checkpoint_dir / f"{safe_name}.json"

    checkpoint_state = read_checkpoint(checkpoint_path)
    if resume and checkpoint_state.exists and not checkpoint_state.valid:
        _emit_api_log(
            api_log_callback,
            {
                "level": "summary",
                "event": "checkpoint_invalid",
                "endpoint": spec.name,
                "checkpoint_file": str(checkpoint_path),
                "reason": checkpoint_state.invalid_reason,
            },
        )
        raise FetchError(
            f"Invalid checkpoint for '{spec.name}' at '{checkpoint_path}': "
            f"{checkpoint_state.invalid_reason}. Action: repair/delete checkpoint "
            "or run without --resume."
        )

    checkpoint_skip = checkpoint_state.next_skip if checkpoint_state.valid else 0
    checkpoint_count = checkpoint_state.fetched_count if checkpoint_state.valid else 0
    checkpoint_delta_floor = checkpoint_state.delta_floor if checkpoint_state.valid else None
    checkpoint_completed = checkpoint_state.completed if checkpoint_state.valid else None
    checkpoint_restart_from_zero = (
        checkpoint_state.restart_from_zero if checkpoint_state.valid else False
    )
    checkpoint_window_start_line = (
        checkpoint_state.window_start_line if checkpoint_state.valid else None
    )
    checkpoint_output_file = checkpoint_state.output_file if checkpoint_state.valid else None
    if resume and checkpoint_output_file is not None and checkpoint_completed is not True:
        output_path = checkpoint_output_file
    effective_delta_floor = delta_floor
    start_skip = checkpoint_skip if resume else 0
    items_fetched = checkpoint_count if (resume and checkpoint_skip > 0) else 0
    checkpoint_warning: str | None = None
    force_output_rewrite = False
    checkpoint_completed_resolved = checkpoint_completed
    if checkpoint_completed_resolved is None and resume_completed_hint is not None:
        checkpoint_completed_resolved = resume_completed_hint

    if resume and checkpoint_restart_from_zero:
        force_output_rewrite = True
        start_skip = 0
        items_fetched = 0
        if checkpoint_delta_floor is not None:
            effective_delta_floor = checkpoint_delta_floor

    _emit_api_log(
        api_log_callback,
        {
            "level": "debug",
            "event": "checkpoint_state_loaded",
            "endpoint": spec.name,
            "checkpoint_file": str(checkpoint_path),
            "checkpoint_exists": checkpoint_state.exists,
            "checkpoint_valid": checkpoint_state.valid,
            "checkpoint_next_skip": checkpoint_skip,
            "checkpoint_fetched_count": checkpoint_count,
            "checkpoint_delta_floor": checkpoint_delta_floor,
            "checkpoint_completed": checkpoint_completed_resolved,
            "checkpoint_restart_from_zero": checkpoint_restart_from_zero,
            "checkpoint_window_start_line": checkpoint_window_start_line,
            "resume": resume,
            "delta_floor": delta_floor,
            "force_output_rewrite": force_output_rewrite,
        },
    )

    if resume and checkpoint_completed_resolved is True:
        message = (
            f"Fetch already completed for '{spec.name}'"
            + (
                " ("
                f"checkpoint delta_floor={checkpoint_delta_floor}, "
                f"current delta_floor={delta_floor}"
                ")"
                if checkpoint_delta_floor is not None or delta_floor is not None
                else ""
            )
            + ". Run without --resume to start a new window."
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "summary",
                "event": "resume_completed_checkpoint_failed",
                "endpoint": spec.name,
                "start_skip": start_skip,
                "checkpoint_delta_floor": checkpoint_delta_floor,
                "current_delta_floor": delta_floor,
                "message": message,
            },
        )
        raise FetchError(message)

    if (
        resume
        and start_skip > 0
        and delta_floor is not None
        and checkpoint_delta_floor is not None
        and checkpoint_delta_floor != delta_floor
    ):
        checkpoint_warning = (
            f"Checkpoint delta floor mismatch for '{spec.name}': "
            f"checkpoint={checkpoint_delta_floor} "
            f"current={delta_floor}. Resume continues with checkpoint delta floor."
        )
        warnings.append(checkpoint_warning)
        _emit_api_log(
            api_log_callback,
            {
                "level": "debug",
                "event": "delta_floor_mismatch",
                "endpoint": spec.name,
                "checkpoint_delta_floor": checkpoint_delta_floor,
                "current_delta_floor": delta_floor,
            },
        )
        effective_delta_floor = checkpoint_delta_floor

    spec = build_delta_endpoint_spec(spec, effective_delta_floor)
    _emit_api_log(
        api_log_callback,
        {
            "level": "debug",
            "event": "runtime_query_prepared",
            "endpoint": spec.name,
            "effective_delta_floor": effective_delta_floor,
            "data_sort": spec.query_params.get("sort"),
        },
    )

    mode = "w" if force_output_rewrite else ("a" if (start_skip > 0 or append_output) else "w")
    window_start_line = 0
    if mode == "a":
        if resume and start_skip > 0:
            # For resumed windows, continue using the original invocation anchor.
            window_start_line = checkpoint_window_start_line or 0
        else:
            window_start_line = count_file_lines(output_path)

    if resume:
        write_checkpoint(
            checkpoint_path,
            spec.name,
            start_skip,
            items_fetched,
            delta_floor=effective_delta_floor,
            completed=False,
            restart_from_zero=False,
            window_start_line=window_start_line,
            output_file=output_path,
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "debug",
                "event": "checkpoint_written",
                "endpoint": spec.name,
                "checkpoint_file": str(checkpoint_path),
                "next_skip": start_skip,
                "fetched_count": items_fetched,
                "completed": False,
                "delta_floor": effective_delta_floor,
                "restart_from_zero": False,
                "window_start_line": window_start_line,
            },
        )

    expected_count = get_expected_count(
        spec,
        auth_ctx,
        fetcher_cfg,
        retries_used_ref=retries_used_ref,
        progress_callback=progress_callback,
        api_log_callback=api_log_callback,
    )

    _emit_progress(
        progress_callback,
        FetchProgressEvent(
            kind="endpoint_start",
            endpoint=spec.name,
            delta_floor=effective_delta_floor,
            modified_since=modified_since,
            resumed=resume,
            start_skip=start_skip,
            limit=spec.limit,
            expected_count=expected_count,
            retries_used=retries_used_ref[0],
            elapsed_seconds=time.time() - started,
        ),
    )

    if checkpoint_warning:
        _emit_progress(
            progress_callback,
            FetchProgressEvent(
                kind="warning",
                endpoint=spec.name,
                message=checkpoint_warning,
                retries_used=retries_used_ref[0],
                elapsed_seconds=time.time() - started,
            ),
        )

    pages_fetched = 0
    next_skip = start_skip
    expected_pages = math.ceil(expected_count / spec.limit)
    page_durations: deque[float] = deque(maxlen=10)

    def _fail_integrity(message: str) -> None:
        _emit_progress(
            progress_callback,
            FetchProgressEvent(
                kind="warning",
                endpoint=spec.name,
                message=message,
                pages_fetched=pages_fetched,
                items_fetched=items_fetched,
                expected_count=expected_count,
                retries_used=retries_used_ref[0],
                elapsed_seconds=time.time() - started,
            ),
        )
        write_checkpoint(
            checkpoint_path,
            spec.name,
            0,
            0,
            delta_floor=effective_delta_floor,
            completed=False,
            restart_from_zero=True,
            window_start_line=0,
            output_file=output_path,
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "summary",
                "event": "integrity_failure_checkpoint_marked",
                "endpoint": spec.name,
                "checkpoint_file": str(checkpoint_path),
                "next_skip": 0,
                "fetched_count": 0,
                "completed": False,
                "restart_from_zero": True,
                "window_start_line": 0,
                "delta_floor": effective_delta_floor,
            },
        )
        raise FetchError(message)

    tracked_id_items = 0
    seen_ids: set[Any] = set()
    duplicate_ids: list[Any] = []
    duplicate_id_set: set[Any] = set()
    invalid_id_count = 0
    first_invalid_id_detail: str | None = None
    count_validation_status = "passed"
    count_validation_reason: str | None = None

    def _finish_success(*, output_file_created: bool) -> FetchRunResult:
        id_validation_status = "passed"
        id_validation_checked_items = tracked_id_items
        id_validation_unique_ids = len(seen_ids)
        id_validation_reason = None
        _emit_api_log(
            api_log_callback,
            {
                "level": "debug",
                "event": "id_validation_passed",
                "endpoint": spec.name,
                "checked_items": id_validation_checked_items,
                "unique_ids": id_validation_unique_ids,
            },
        )

        duration = time.time() - started
        write_checkpoint(
            checkpoint_path,
            spec.name,
            next_skip,
            items_fetched,
            delta_floor=effective_delta_floor,
            completed=True,
            restart_from_zero=False,
            window_start_line=window_start_line,
            output_file=output_path if output_file_created else None,
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "debug",
                "event": "checkpoint_written",
                "endpoint": spec.name,
                "checkpoint_file": str(checkpoint_path),
                "next_skip": next_skip,
                "fetched_count": items_fetched,
                "completed": True,
                "delta_floor": effective_delta_floor,
                "restart_from_zero": False,
                "window_start_line": window_start_line,
                "output_file_created": output_file_created,
            },
        )
        _emit_progress(
            progress_callback,
            FetchProgressEvent(
                kind="endpoint_finish",
                endpoint=spec.name,
                delta_floor=effective_delta_floor,
                modified_since=modified_since,
                resumed=resume,
                start_skip=start_skip,
                pages_fetched=pages_fetched,
                items_fetched=items_fetched,
                expected_count=expected_count,
                retries_used=retries_used_ref[0],
                warnings_count=len(warnings),
                elapsed_seconds=duration,
            ),
        )
        return FetchRunResult(
            endpoint=spec.name,
            pages_fetched=pages_fetched,
            items_fetched=items_fetched,
            expected_count=expected_count,
            retries_used=retries_used_ref[0],
            start_skip=start_skip,
            next_skip=next_skip,
            duration_seconds=duration,
            output_file=output_path,
            checkpoint_file=checkpoint_path,
            output_file_created=output_file_created,
            warnings=warnings,
            effective_delta_floor=effective_delta_floor,
            did_catch_up=resume,
            count_validation_status=count_validation_status,
            count_validation_reason=count_validation_reason,
            id_validation_status=id_validation_status,
            id_validation_checked_items=id_validation_checked_items,
            id_validation_unique_ids=id_validation_unique_ids,
            id_validation_reason=id_validation_reason,
        )

    if resume and start_skip > 0:
        try:
            (
                seeded_items,
                seeded_invalid_count,
                seeded_first_invalid_detail,
            ) = seed_resume_window_id_state(
                output_path,
                endpoint_name=spec.name,
                window_start_line=window_start_line,
                fetched_count=items_fetched,
                seen_ids=seen_ids,
                duplicate_id_set=duplicate_id_set,
                duplicate_ids=duplicate_ids,
            )
        except FetchError as exc:
            _emit_api_log(
                api_log_callback,
                {
                    "level": "summary",
                    "event": "id_validation_seed_failed",
                    "endpoint": spec.name,
                    "error": str(exc),
                },
            )
            _fail_integrity(
                f"Post-fetch ID validation failed for '{spec.name}': "
                "unable to validate resumed window state "
                f"({exc}). Action: exit endpoint."
            )

        tracked_id_items += seeded_items
        invalid_id_count += seeded_invalid_count
        if first_invalid_id_detail is None and seeded_first_invalid_detail is not None:
            first_invalid_id_detail = seeded_first_invalid_detail
        _emit_api_log(
            api_log_callback,
            {
                "level": "debug",
                "event": "id_validation_seeded",
                "endpoint": spec.name,
                "seeded_items": seeded_items,
                "window_start_line": window_start_line,
                "window_item_count": items_fetched,
            },
        )
        truncate_file_lines(output_path, window_start_line + items_fetched)
        _emit_api_log(
            api_log_callback,
            {
                "level": "debug",
                "event": "resume_output_truncated",
                "endpoint": spec.name,
                "output_file": str(output_path),
                "line_count": window_start_line + items_fetched,
            },
        )

    if expected_count == 0 and items_fetched == 0 and not create_empty_output:
        return _finish_success(output_file_created=False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open(mode, encoding="utf-8") as out_fh:
        for page in iter_pages(
            spec,
            auth_ctx,
            fetcher_cfg,
            start_skip=start_skip,
            already_fetched=items_fetched,
            expected_total=expected_count,
            retries_used_ref=retries_used_ref,
            progress_callback=progress_callback,
            api_log_callback=api_log_callback,
        ):
            pages_fetched += 1
            for item in page.items:
                out_fh.write(json.dumps(item, separators=(",", ":")) + "\n")
                tracked_id_items += 1
                invalid_detail = track_item_id(
                    item,
                    seen_ids=seen_ids,
                    duplicate_id_set=duplicate_id_set,
                    duplicate_ids=duplicate_ids,
                )
                if invalid_detail is not None:
                    invalid_id_count += 1
                    if first_invalid_id_detail is None:
                        first_invalid_id_detail = invalid_detail
            items_fetched += len(page.items)
            next_skip = page.skip + spec.limit
            page_durations.append(page.duration_seconds)
            rolling_avg_seconds = sum(page_durations) / len(page_durations)
            remaining_items = max(expected_count - items_fetched, 0)
            remaining_pages = math.ceil(remaining_items / spec.limit)
            estimated_remaining_seconds = remaining_pages * rolling_avg_seconds
            write_checkpoint(
                checkpoint_path,
                spec.name,
                next_skip,
                items_fetched,
                delta_floor=effective_delta_floor,
                completed=False,
                restart_from_zero=False,
                window_start_line=window_start_line,
                output_file=output_path,
            )
            _emit_api_log(
                api_log_callback,
                {
                    "level": "debug",
                    "event": "checkpoint_written",
                    "endpoint": spec.name,
                    "checkpoint_file": str(checkpoint_path),
                    "next_skip": next_skip,
                    "fetched_count": items_fetched,
                    "completed": False,
                    "delta_floor": effective_delta_floor,
                    "restart_from_zero": False,
                    "window_start_line": window_start_line,
                },
            )
            percent_complete = (
                min(100.0, (items_fetched / expected_count) * 100.0) if expected_count > 0 else None
            )
            _emit_progress(
                progress_callback,
                FetchProgressEvent(
                    kind="page_fetched",
                    endpoint=spec.name,
                    page_index=pages_fetched,
                    page_items=len(page.items),
                    pages_fetched=pages_fetched,
                    items_fetched=items_fetched,
                    skip=page.skip,
                    next_skip=next_skip,
                    expected_count=expected_count,
                    expected_pages=expected_pages,
                    percent_complete=percent_complete,
                    page_duration_seconds=page.duration_seconds,
                    rolling_avg_seconds=rolling_avg_seconds,
                    estimated_remaining_seconds=estimated_remaining_seconds,
                    retries_used=retries_used_ref[0],
                    elapsed_seconds=time.time() - started,
                ),
            )

    if items_fetched != expected_count:
        mismatch_error = (
            f"Fetched {items_fetched} items for '{spec.name}' "
            f"but count preflight expected {expected_count}."
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "summary",
                "event": "count_mismatch_failed",
                "endpoint": spec.name,
                "expected_count": expected_count,
                "items_fetched": items_fetched,
            },
        )
        _fail_integrity(
            f"{mismatch_error} Post-fetch integrity requires expected count to match actual count. "
            "Action: exit endpoint."
        )

    if invalid_id_count > 0 or duplicate_ids:
        detail_parts: list[str] = []
        if invalid_id_count > 0:
            first_issue = first_invalid_id_detail or "invalid field 'id'"
            detail_parts.append(
                f"invalid id values={invalid_id_count} (first issue: {first_issue})"
            )
        if duplicate_ids:
            duplicate_preview = [repr(value) for value in duplicate_ids[:5]]
            detail_parts.append(
                f"duplicate ids={len(duplicate_ids)} (sample: {', '.join(duplicate_preview)})"
            )
        validation_error = (
            f"Post-fetch ID validation failed for '{spec.name}': "
            + "; ".join(detail_parts)
            + ". Duplicate IDs indicate unstable pagination. Action: exit endpoint."
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "summary",
                "event": "id_validation_failed",
                "endpoint": spec.name,
                "invalid_id_count": invalid_id_count,
                "duplicate_id_count": len(duplicate_ids),
                "duplicate_ids_sample": [repr(value) for value in duplicate_ids[:5]],
            },
        )
        _fail_integrity(validation_error)

    unique_id_count = len(seen_ids)
    if unique_id_count != expected_count:
        _emit_api_log(
            api_log_callback,
            {
                "level": "summary",
                "event": "unique_id_count_mismatch_failed",
                "endpoint": spec.name,
                "expected_count": expected_count,
                "checked_items": tracked_id_items,
                "unique_ids": unique_id_count,
            },
        )
        _fail_integrity(
            f"Post-fetch ID validation failed for '{spec.name}': "
            f"expected {expected_count} unique ids, found {unique_id_count}. "
            "Action: exit endpoint."
        )

    return _finish_success(output_file_created=True)
