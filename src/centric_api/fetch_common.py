from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from .auth import AuthContext
from .models import FetcherConfig, FetchProgressEvent


class FetchError(RuntimeError):
    pass


_TRANSIENT_STATUSES = {429}
_PATH_RE = re.compile(r"(?:\.([A-Za-z_][A-Za-z0-9_]*))|(?:\[(\d+)\])")
_OPERATOR_SUFFIXES = {"!", "ge", "gt", "le", "lt"}

RequestParams = dict[str, Any] | list[tuple[str, Any]]
ApiLogEvent = dict[str, Any]
ApiLogCallback = Callable[[ApiLogEvent], None] | None
PAGINATION_SKIP_PARAM = "skip"
PAGINATION_LIMIT_PARAM = "limit"
ITEM_PATH = "$"
COUNT_RESULT_PATH = "$.count"
COUNT_API_VERSION = "v2"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _parse_path(path: str) -> list[tuple[str, Any]]:
    if path == "$":
        return []
    if not path.startswith("$"):
        raise FetchError(f"Invalid JSON path: {path}")

    tokens: list[tuple[str, Any]] = []
    index = 1
    while index < len(path):
        match = _PATH_RE.match(path, index)
        if not match:
            raise FetchError(f"Unsupported JSON path segment near '{path[index:]}'")
        key, idx = match.groups()
        if key is not None:
            tokens.append(("key", key))
        elif idx is not None:
            tokens.append(("idx", int(idx)))
        index = match.end()
    return tokens


def extract_json_path(payload: Any, path: str) -> Any:
    current = payload
    for kind, value in _parse_path(path):
        if kind == "key":
            if not isinstance(current, dict) or value not in current:
                raise FetchError(f"JSON path not found: {path}")
            current = current[value]
        else:
            if not isinstance(current, list) or value >= len(current):
                raise FetchError(f"JSON path not found: {path}")
            current = current[value]
    return current


def _is_transient_status(status_code: int) -> bool:
    return status_code in _TRANSIENT_STATUSES or 500 <= status_code <= 599


def _is_transient_exception(exc: Exception) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _backoff_seconds(fetcher_cfg: FetcherConfig, attempt: int) -> float:
    base = fetcher_cfg.retry_base_seconds * (2 ** (attempt - 1))
    return min(base, fetcher_cfg.retry_max_seconds)


def _build_endpoint_url(base_url: str, api_version: str, path: str) -> str:
    normalized = path.strip().strip("/")
    return f"{base_url}/api/{api_version}/{normalized}"


def _compile_query_params(query_params: dict[str, Any]) -> list[tuple[str, Any]]:
    params: list[tuple[str, Any]] = []
    has_decoded = False
    for raw_key, value in query_params.items():
        key = str(raw_key)
        field, sep, suffix = key.rpartition("=")
        if key == "decoded" or (sep and field == "decoded" and suffix == ""):
            has_decoded = True
        if sep and field and suffix in _OPERATOR_SUFFIXES:
            params.append((field, f"{suffix}{value}"))
        elif sep and field and suffix == "":
            # Treat trailing "=" as plain equality (e.g. "active=" -> "active").
            params.append((field, value))
        else:
            params.append((key, value))
    if not has_decoded:
        params.append(("decoded", True))
    return params


def _with_pagination_params(
    params: list[tuple[str, Any]],
    *,
    skip: int,
    limit: int,
) -> list[tuple[str, Any]]:
    base_params = [
        (key, value)
        for key, value in params
        if key not in {PAGINATION_SKIP_PARAM, PAGINATION_LIMIT_PARAM}
    ]
    base_params.append((PAGINATION_SKIP_PARAM, skip))
    base_params.append((PAGINATION_LIMIT_PARAM, limit))
    return base_params


def _emit_api_log(api_log_callback: ApiLogCallback, event: ApiLogEvent) -> None:
    if api_log_callback is not None:
        api_log_callback(event)


def _monotonic_seconds() -> float:
    return time.perf_counter()


def _format_request_url(url: str, params: RequestParams | None) -> str:
    if not params:
        return url
    return str(httpx.URL(url).copy_merge_params(params))


def _request_json_with_retry(
    auth_ctx: AuthContext,
    *,
    method: str,
    url: str,
    params: RequestParams | None,
    fetcher_cfg: FetcherConfig,
    retries_used_ref: list[int],
    progress_callback: Callable[[FetchProgressEvent], None] | None = None,
    endpoint_name: str | None = None,
    request_kind: str = "request",
    api_log_callback: ApiLogCallback = None,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, fetcher_cfg.retry_max_attempts + 1):
        request_url = _format_request_url(url, params)
        _emit_api_log(
            api_log_callback,
            {
                "level": "http",
                "event": "http_request",
                "endpoint": endpoint_name,
                "request_kind": request_kind,
                "method": method.upper(),
                "url": request_url,
                "attempt": attempt,
                "max_attempts": fetcher_cfg.retry_max_attempts,
            },
        )
        try:
            request_started = _monotonic_seconds()
            response = auth_ctx.request(method, url, params=params)
            request_duration = _monotonic_seconds() - request_started
        except Exception as exc:  # pragma: no cover - explicit branch tested indirectly
            if _is_transient_exception(exc):
                last_error = exc
                if attempt < fetcher_cfg.retry_max_attempts:
                    retries_used_ref[0] += 1
                    sleep_seconds = _backoff_seconds(fetcher_cfg, attempt)
                    _emit_api_log(
                        api_log_callback,
                        {
                            "level": "debug",
                            "event": "retry_scheduled",
                            "endpoint": endpoint_name,
                            "request_kind": request_kind,
                            "reason": "transient_transport_error",
                            "error": str(exc),
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "max_attempts": fetcher_cfg.retry_max_attempts,
                            "sleep_seconds": round(sleep_seconds, 3),
                        },
                    )
                    _emit_progress(
                        progress_callback,
                        FetchProgressEvent(
                            kind="warning",
                            endpoint=endpoint_name or "unknown",
                            retries_used=retries_used_ref[0],
                            message=(
                                f"{request_kind} transient transport error on attempt "
                                f"{attempt}/{fetcher_cfg.retry_max_attempts}: {exc}. "
                                "Action: retrying."
                            ),
                        ),
                    )
                    time.sleep(sleep_seconds)
                    continue
                _emit_api_log(
                    api_log_callback,
                    {
                        "level": "summary",
                        "event": "request_failed",
                        "endpoint": endpoint_name,
                        "request_kind": request_kind,
                        "reason": "transport_error_after_retries",
                        "error": str(exc),
                        "attempt": attempt,
                        "max_attempts": fetcher_cfg.retry_max_attempts,
                    },
                )
                raise FetchError(
                    f"{request_kind} failed after retries due to transport error: {exc}. "
                    "Action: exit endpoint."
                ) from exc
            raise FetchError(str(exc)) from exc

        _emit_api_log(
            api_log_callback,
            {
                "level": "http",
                "event": "http_response",
                "endpoint": endpoint_name,
                "request_kind": request_kind,
                "method": method.upper(),
                "url": request_url,
                "attempt": attempt,
                "max_attempts": fetcher_cfg.retry_max_attempts,
                "status_code": response.status_code,
                "reason_phrase": response.reason_phrase or "",
                "duration_seconds": round(request_duration, 3),
            },
        )

        if response.status_code >= 400:
            if _is_transient_status(response.status_code):
                if attempt < fetcher_cfg.retry_max_attempts:
                    retries_used_ref[0] += 1
                    sleep_seconds = _backoff_seconds(fetcher_cfg, attempt)
                    _emit_api_log(
                        api_log_callback,
                        {
                            "level": "debug",
                            "event": "retry_scheduled",
                            "endpoint": endpoint_name,
                            "request_kind": request_kind,
                            "reason": "transient_http_status",
                            "status_code": response.status_code,
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "max_attempts": fetcher_cfg.retry_max_attempts,
                            "sleep_seconds": round(sleep_seconds, 3),
                        },
                    )
                    _emit_progress(
                        progress_callback,
                        FetchProgressEvent(
                            kind="warning",
                            endpoint=endpoint_name or "unknown",
                            retries_used=retries_used_ref[0],
                            message=(
                                f"{request_kind} got transient HTTP {response.status_code} "
                                "on attempt "
                                f"{attempt}/{fetcher_cfg.retry_max_attempts}. Action: retrying."
                            ),
                        ),
                    )
                    time.sleep(sleep_seconds)
                    continue
                _emit_api_log(
                    api_log_callback,
                    {
                        "level": "summary",
                        "event": "request_failed",
                        "endpoint": endpoint_name,
                        "request_kind": request_kind,
                        "reason": "transient_http_status_after_retries",
                        "status_code": response.status_code,
                        "attempt": attempt,
                        "max_attempts": fetcher_cfg.retry_max_attempts,
                    },
                )
                raise FetchError(
                    f"{request_kind} failed after retries (HTTP {response.status_code}; "
                    f"{_summarize_response_body(response.text)}). Action: exit endpoint."
                )
            _emit_api_log(
                api_log_callback,
                {
                    "level": "summary",
                    "event": "request_failed",
                    "endpoint": endpoint_name,
                    "request_kind": request_kind,
                    "reason": "non_retryable_http_status",
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "max_attempts": fetcher_cfg.retry_max_attempts,
                },
            )
            raise FetchError(
                f"{request_kind} failed with non-retryable HTTP {response.status_code} "
                f"({_summarize_response_body(response.text)}). Action: exit endpoint."
            )

        try:
            return response.json()
        except Exception as exc:
            raise FetchError(f"Failed to parse JSON response: {exc}") from exc

    if last_error:
        raise FetchError(f"Request failed: {last_error}")
    raise FetchError("Request failed unexpectedly.")


def _emit_progress(
    progress_callback: Callable[[FetchProgressEvent], None] | None,
    event: FetchProgressEvent,
) -> None:
    if progress_callback is not None:
        progress_callback(event)


def _summarize_response_body(response_text: str) -> str:
    text = response_text.strip()
    if not text:
        return "empty response body"

    try:
        payload = json.loads(text)
    except Exception:
        flattened = " ".join(text.split())
        preview = flattened[:180]
        if len(flattened) > 180:
            preview += "..."
        return f"non-JSON body preview={preview!r}"

    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "details"):
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)):
                return f"json {key}={value!r}"
        return f"json object keys={list(payload.keys())[:5]}"
    if isinstance(payload, list):
        return f"json array items={len(payload)}"
    return f"json {type(payload).__name__}"


def _extract_items(payload: Any) -> list[dict]:
    raw = extract_json_path(payload, ITEM_PATH)
    if not isinstance(raw, list):
        raise FetchError(f"item path '{ITEM_PATH}' did not resolve to an array.")

    items: list[dict] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise FetchError(f"Item at index {idx} is not an object.")
        items.append(item)
    return items
