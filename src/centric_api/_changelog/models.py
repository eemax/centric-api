from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from ..record_constants import (
    DELETE_TYPE_HARD_DELETE,
    DELETE_TYPE_TOMBSTONE,
    DELETE_TYPE_UNKNOWN,
    MODIFIED_AT_FIELD,
    MODIFIED_BY_FIELD,
    USER_ENDPOINT,
    USER_NAME_FIELD,
)

CHANGELOG_SOURCE = "payload-hash"
CHANGELOG_SOURCE_SHA = hashlib.sha256(CHANGELOG_SOURCE.encode("utf-8")).hexdigest()
ACTIVITY_AT_SQL = "COALESCE(modified_at, changed_at)"
ProgressCallback = Callable[[str], None]

__all__ = [
    "ACTIVITY_AT_SQL",
    "CHANGELOG_SOURCE",
    "CHANGELOG_SOURCE_SHA",
    "ChangelogRun",
    "DELETE_TYPE_HARD_DELETE",
    "DELETE_TYPE_TOMBSTONE",
    "DELETE_TYPE_UNKNOWN",
    "MODIFIED_AT_FIELD",
    "MODIFIED_BY_FIELD",
    "ProgressCallback",
    "USER_ENDPOINT",
    "USER_NAME_FIELD",
]


@dataclass(frozen=True)
class ChangelogRun:
    run_id: str
    endpoint_count: int
    record_count: int
    event_count: int
    full_refresh: bool
    scoped_record_count: int


@dataclass(frozen=True)
class _IndexRow:
    endpoint: str
    record_id: str
    payload_hash: str
    payload_json: str | None


@dataclass(frozen=True)
class _ChangeEvent:
    run_id: str
    endpoint: str
    record_id: str
    changed_at: str
    change_type: str
    delete_type: str | None
    modified_at: str | None
    modified_by_id: str | None
    modified_by_name: str | None
    previous_hash: str | None
    current_hash: str | None
    changed_fields: list[str]
    previous_payload_json: str | None
    current_payload_json: str | None
