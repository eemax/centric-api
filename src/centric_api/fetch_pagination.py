from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .auth import AuthContext
from .fetch_common import (
    COUNT_API_VERSION,
    COUNT_RESULT_PATH,
    ApiLogCallback,
    FetchError,
    _build_endpoint_url,
    _compile_query_params,
    _emit_api_log,
    _extract_items,
    _monotonic_seconds,
    _request_json_with_retry,
    _with_pagination_params,
    extract_json_path,
)
from .models import EndpointSpec, FetcherConfig, FetchProgressEvent


@dataclass
class Page:
    skip: int
    items: list[dict]
    duration_seconds: float


def iter_pages(
    spec: EndpointSpec,
    auth_ctx: AuthContext,
    fetcher_cfg: FetcherConfig,
    *,
    start_skip: int,
    already_fetched: int,
    expected_total: int,
    retries_used_ref: list[int],
    progress_callback: Callable[[FetchProgressEvent], None] | None = None,
    api_log_callback: ApiLogCallback = None,
) -> Iterator[Page]:
    skip = start_skip
    fetched = already_fetched
    url = _build_endpoint_url(
        fetcher_cfg.base_url or auth_ctx.base_url,
        spec.api_version,
        spec.path,
    )
    base_params = _compile_query_params(spec.query_params)

    if expected_total == 0:
        return

    while True:
        params = _with_pagination_params(
            base_params,
            skip=skip,
            limit=spec.limit,
        )

        page_started = _monotonic_seconds()
        payload = _request_json_with_retry(
            auth_ctx,
            method="GET",
            url=url,
            params=params,
            fetcher_cfg=fetcher_cfg,
            retries_used_ref=retries_used_ref,
            progress_callback=progress_callback,
            endpoint_name=spec.name,
            request_kind="data fetch",
            api_log_callback=api_log_callback,
        )
        items = _extract_items(payload)
        page = Page(
            skip=skip,
            items=items,
            duration_seconds=_monotonic_seconds() - page_started,
        )
        _emit_api_log(
            api_log_callback,
            {
                "level": "http",
                "event": "data_page",
                "endpoint": spec.name,
                "skip": skip,
                "limit": spec.limit,
                "items": len(items),
                "duration_seconds": round(page.duration_seconds, 3),
            },
        )
        yield page

        fetched += len(items)
        if fetched >= expected_total:
            break
        if not items or len(items) < spec.limit:
            break
        skip += spec.limit


def get_expected_count(
    spec: EndpointSpec,
    auth_ctx: AuthContext,
    fetcher_cfg: FetcherConfig,
    retries_used_ref: list[int] | None = None,
    progress_callback: Callable[[FetchProgressEvent], None] | None = None,
    api_log_callback: ApiLogCallback = None,
) -> int:
    retries_ref = retries_used_ref if retries_used_ref is not None else [0]
    count_url = _build_endpoint_url(
        fetcher_cfg.base_url or auth_ctx.base_url,
        COUNT_API_VERSION,
        spec.count_spec.path,
    )
    payload = _request_json_with_retry(
        auth_ctx,
        method="GET",
        url=count_url,
        params=_compile_query_params(spec.count_spec.query_params),
        fetcher_cfg=fetcher_cfg,
        retries_used_ref=retries_ref,
        progress_callback=progress_callback,
        endpoint_name=spec.name,
        request_kind="count preflight",
        api_log_callback=api_log_callback,
    )
    result = extract_json_path(payload, COUNT_RESULT_PATH)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise FetchError(f"Count path '{COUNT_RESULT_PATH}' did not resolve to a number.")
    if isinstance(result, float) and not result.is_integer():
        raise FetchError(f"Count path '{COUNT_RESULT_PATH}' resolved to a non-integer number.")
    if result < 0:
        raise FetchError("Count result cannot be negative.")
    expected_count = int(result)
    _emit_api_log(
        api_log_callback,
        {
            "level": "http",
            "event": "count_preflight",
            "endpoint": spec.name,
            "expected": expected_count,
        },
    )
    return expected_count
