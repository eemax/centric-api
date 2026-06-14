from __future__ import annotations

from centric_api import changelog, store
from centric_api import record_constants as constants


def test_store_and_changelog_share_record_constants() -> None:
    assert store.PRIMARY_KEY_FIELD is constants.PRIMARY_KEY_FIELD
    assert store.MODIFIED_AT_FIELD is constants.MODIFIED_AT_FIELD
    assert store.DELETE_TYPE_TOMBSTONE is constants.DELETE_TYPE_TOMBSTONE
    assert store.DELETE_TYPE_HARD_DELETE is constants.DELETE_TYPE_HARD_DELETE
    assert store.HARD_DELETE_TYPE_FIELD is constants.HARD_DELETE_TYPE_FIELD

    assert changelog.MODIFIED_AT_FIELD is constants.MODIFIED_AT_FIELD
    assert changelog.MODIFIED_BY_FIELD is constants.MODIFIED_BY_FIELD
    assert changelog.USER_ENDPOINT is constants.USER_ENDPOINT
    assert changelog.USER_NAME_FIELD is constants.USER_NAME_FIELD
    assert changelog.DELETE_TYPE_TOMBSTONE is constants.DELETE_TYPE_TOMBSTONE
    assert changelog.DELETE_TYPE_HARD_DELETE is constants.DELETE_TYPE_HARD_DELETE
    assert changelog.DELETE_TYPE_UNKNOWN is constants.DELETE_TYPE_UNKNOWN
