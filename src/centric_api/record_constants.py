from __future__ import annotations

PRIMARY_KEY_FIELD = "id"
MODIFIED_AT_FIELD = "_modified_at"
MODIFIED_BY_FIELD = "modified_by"
USER_ENDPOINT = "users"
USER_NAME_FIELD = "node_name"

HARD_DELETE_TYPE_FIELD = "_centric_api_delete_type"
HARD_DELETE_DELETED_AT_FIELD = "_centric_api_deleted_at"
HARD_DELETE_SOURCE_RUN_ID_FIELD = "_centric_api_source_run_id"
HARD_DELETE_SOURCE_FILE_FIELD = "_centric_api_source_file"

DELETE_TYPE_TOMBSTONE = "tombstone"
DELETE_TYPE_HARD_DELETE = "hard_delete"
DELETE_TYPE_UNKNOWN = "unknown"

__all__ = [
    "DELETE_TYPE_HARD_DELETE",
    "DELETE_TYPE_TOMBSTONE",
    "DELETE_TYPE_UNKNOWN",
    "HARD_DELETE_DELETED_AT_FIELD",
    "HARD_DELETE_SOURCE_FILE_FIELD",
    "HARD_DELETE_SOURCE_RUN_ID_FIELD",
    "HARD_DELETE_TYPE_FIELD",
    "MODIFIED_AT_FIELD",
    "MODIFIED_BY_FIELD",
    "PRIMARY_KEY_FIELD",
    "USER_ENDPOINT",
    "USER_NAME_FIELD",
]
