from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

CHANGELOG_SOURCE = "full-payload"
CHANGELOG_SOURCE_SHA = hashlib.sha256(CHANGELOG_SOURCE.encode("utf-8")).hexdigest()
MODIFIED_BY_FIELD = "modified_by"
MODIFIED_AT_FIELD = "_modified_at"
USER_ENDPOINT = "users"
USER_NAME_FIELD = "node_name"
DELETE_TYPE_TOMBSTONE = "tombstone"
DELETE_TYPE_HARD_DELETE = "hard_delete"
DELETE_TYPE_UNKNOWN = "unknown"
ACTIVITY_AT_SQL = "COALESCE(modified_at, changed_at)"
ProgressCallback = Callable[[str], None]


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
    payload_json: str


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
