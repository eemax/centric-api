from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .config import HttpSettings

CENTRIC_DOMAIN = "centricsoftware.com"
REQUEST_HANDLER_PATH = "/csi-requesthandler"
TRANSIENT_STATUSES = {408, 429}
MAX_RETRY_AFTER_SECONDS = 300
OPERATOR_SUFFIXES = {"!", "ge", "gt", "le", "lt"}


class AuthError(RuntimeError):
    pass


class FetchError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Credentials:
    base_url: str
    username: str
    password: str


def load_credentials(env_file: Path | None) -> Credentials:
    """Read CENTRIC_BASE_URL/USERNAME/PASSWORD; environment variables win over the env file."""
    file_values = _read_env_file(env_file) if env_file is not None else {}

    def value(key: str) -> str | None:
        raw = os.environ.get(key)
        if raw is None:
            raw = file_values.get(key)
        return raw.strip() if raw and raw.strip() else None

    base_url = value("CENTRIC_BASE_URL")
    username = value("CENTRIC_USERNAME")
    password = value("CENTRIC_PASSWORD")
    missing = [
        key
        for key, item in (
            ("CENTRIC_BASE_URL", base_url),
            ("CENTRIC_USERNAME", username),
            ("CENTRIC_PASSWORD", password),
        )
        if item is None
    ]
    if missing:
        source = f" or {env_file}" if env_file is not None else ""
        raise AuthError(f"Missing {', '.join(missing)} in the environment{source}.")
    return Credentials(normalize_base_url(base_url or ""), username or "", password or "")


def normalize_base_url(value: str) -> str:
    """Accept 'acme', 'acme.centricsoftware.com' or a full URL; return the API root URL."""
    raw = value.strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        host, separator, path = raw.strip("/").partition("/")
        if "." not in host:
            host = f"{host}.{CENTRIC_DOMAIN}"
        raw = f"https://{host}" + (f"/{path.rstrip('/')}" if separator else "")
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/")
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0].casefold()
    is_centric_host = host == CENTRIC_DOMAIN or host.endswith(f".{CENTRIC_DOMAIN}")
    if is_centric_host and path.casefold() in {"", REQUEST_HANDLER_PATH}:
        path = REQUEST_HANDLER_PATH
    return urlunsplit((parsed.scheme or "https", parsed.netloc, path, "", ""))


class CentricClient:
    """Authenticated JSON client with retries for transient failures.

    The session token lives only in memory for the duration of a run.
    """

    def __init__(
        self,
        credentials: Credentials,
        http: HttpSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_retry: Callable[[str], None] | None = None,
    ) -> None:
        self.credentials = credentials
        self.http = http
        self._client = httpx.Client(timeout=http.timeout, transport=transport)
        self._token: str | None = None
        self._sleep = sleep
        self._on_retry = on_retry
        self.retries = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CentricClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def url(self, api_version: str, path: str) -> str:
        return f"{self.credentials.base_url}/api/{api_version}/{path.strip('/')}"

    def get_json(self, url: str, params: list[tuple[str, Any]] | None = None) -> Any:
        response = self._with_retries(
            url, lambda: self._authorized_get(url, params), error=FetchError
        )
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"Invalid JSON from {url}: {exc}") from exc

    def _with_retries(
        self,
        url: str,
        send: Callable[[], httpx.Response],
        *,
        error: type[RuntimeError],
    ) -> httpx.Response:
        """Send a request, retrying transport errors and transient HTTP statuses."""
        last_error = ""
        for attempt in range(1, self.http.retry_max_attempts + 1):
            retry_after = None
            try:
                response = send()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"transport error: {exc}"
            else:
                if response.status_code < 400:
                    return response
                if not _is_transient(response.status_code):
                    message = f"HTTP {response.status_code} from {url}: {_summarize(response.text)}"
                    if error is FetchError:
                        raise FetchError(message, status_code=response.status_code)
                    raise error(message)
                last_error = f"HTTP {response.status_code}: {_summarize(response.text)}"
                retry_after = _retry_after_seconds(response)
            if attempt < self.http.retry_max_attempts:
                self.retries += 1
                delay = min(
                    self.http.retry_base_seconds * (2 ** (attempt - 1)),
                    self.http.retry_max_seconds,
                )
                if retry_after is not None:
                    delay = max(delay, retry_after)
                if self._on_retry is not None:
                    self._on_retry(
                        f"{last_error}; retry {attempt}/{self.http.retry_max_attempts - 1} "
                        f"in {delay:g}s"
                    )
                self._sleep(delay)
        raise error(
            f"Request to {url} failed after {self.http.retry_max_attempts} attempts ({last_error})."
        )

    def _authorized_get(self, url: str, params: list[tuple[str, Any]] | None) -> httpx.Response:
        response = self._client.get(url, params=params, headers=self._headers())
        if response.status_code == 401:
            self._token = None
            response = self._client.get(url, params=params, headers=self._headers())
        return response

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = self._login()
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _login(self) -> str:
        url = f"{self.credentials.base_url}/api/v2/session"
        response = self._with_retries(
            url,
            lambda: self._client.post(
                url,
                json={
                    "username": self.credentials.username,
                    "password": self.credentials.password,
                },
            ),
            error=AuthError,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthError("Login response was not valid JSON.") from exc
        token = str(payload.get("token", "")) if isinstance(payload, dict) else ""
        token = token.split("=", 1)[1].strip() if "=" in token else token.strip()
        if not token:
            raise AuthError("Login response did not contain a token.")
        return token


def query_params(raw: dict[str, Any]) -> list[tuple[str, Any]]:
    """Compile endpoint query params; 'field=ge': value becomes ('field', 'ge<value>')."""
    params: list[tuple[str, Any]] = []
    has_decoded = False
    for raw_key, value in raw.items():
        key = str(raw_key)
        field_name, separator, suffix = key.rpartition("=")
        if key == "decoded" or field_name == "decoded":
            has_decoded = True
        if separator and field_name and suffix in OPERATOR_SUFFIXES:
            params.append((field_name, f"{suffix}{_param_value(value)}"))
        elif separator and field_name and suffix == "":
            params.append((field_name, _param_value(value)))
        else:
            params.append((key, _param_value(value)))
    if not has_decoded:
        params.append(("decoded", "true"))
    return params


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise AuthError(f"Env file not found: {path}")
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise AuthError(f"{path}:{number}: expected KEY=value.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _is_transient(status_code: int) -> bool:
    return status_code in TRANSIENT_STATUSES or 500 <= status_code <= 599


def _summarize(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty response body"
    try:
        payload = json.loads(stripped)
    except ValueError:
        flat = " ".join(stripped.split())
        return flat[:180] + ("..." if len(flat) > 180 else "")
    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "details"):
            if isinstance(payload.get(key), str | int | float | bool):
                return f"{key}={payload[key]!r}"
    return stripped[:180]
