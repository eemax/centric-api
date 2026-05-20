from __future__ import annotations

from pathlib import Path

from .config import runtime_path

DEFAULT_CONFIG_PATH = Path("config/fetcher.yml")
DEFAULT_DELTA_STATE_PATH = Path("delta.yml")
DEFAULT_FETCH_LOG_PATH = Path("logs/fetch.log")
DEFAULT_DOWNLOAD_LOG_PATH = Path("logs/download.log")
DEFAULT_DB_PATH = Path("centric.db")
DEFAULT_LOCK_PATH = Path("fetch.lock")
DEFAULT_DOWNLOAD_LOCK_PATH = Path("download.lock")
DEFAULT_BUNDLE_LOCK_PATH = Path("bundle.lock")
DEFAULT_CRON_LOG_PATH = Path("logs/cron.jsonl")
DEFAULT_OVERLAP_MINUTES = 10
DEFAULT_OVERLAP_DAYS = 0
MIN_DAYS_BACK = 1
MAX_DAYS_BACK = 3650
MIN_MONTHS_BACK = 1
MAX_MONTHS_BACK = 120


def db_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else runtime_path(DEFAULT_DB_PATH)
