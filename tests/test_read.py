from __future__ import annotations

import os
import sqlite3

import pytest

from centric_api import read as read_module
from centric_api.db import DatabaseError
from centric_api.fetch import run_fetch
from centric_api.read import Reader
from tests.fake_centric import FakeCentric, make_settings, ts


@pytest.fixture
def fetched(tmp_path):
    fake = FakeCentric()
    fake.add(
        "styles",
        id="S2",
        _modified_at=ts(2),
        node_name="Two",
        active=False,
        tags=["a", "b"],
        nested={"k": [1, {"x": None}]},
        empty=None,
        price=1.5,
        **{'odd."key"': "quoted", "a.b": "dotted", "back\\slash": "bs"},
    )
    fake.add("styles", id="S1", _modified_at=ts(1), node_name="One", active=True)
    fake.add("styles", id="S3", _modified_at=ts(3), node_name="Three", active=True)
    fake.add("colorways", id="C1", _modified_at=ts(1), node_name="Black", style="S1")
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    return fake, settings


def test_full_records_are_the_stored_payloads_in_id_order(fetched) -> None:
    fake, settings = fetched
    with Reader(settings.database) as db:
        records = list(db.records("styles"))
    assert [r["id"] for r in records] == ["S1", "S2", "S3"]
    assert records[1] == next(r for r in fake.records["styles"] if r["id"] == "S2")


# Both parsers must give the same records: orjson (the default) and the stdlib fallback.
@pytest.mark.parametrize("loads", ["orjson", "stdlib"])
def test_fields_select_present_keys_only_and_keep_values_exact(fetched, monkeypatch, loads) -> None:
    if loads == "stdlib":
        monkeypatch.setattr(read_module, "_loads", read_module._loads_stdlib)
    _fake, settings = fetched
    with Reader(settings.database) as db:
        wanted = [
            "node_name",
            "tags",
            "nested",
            "empty",
            "price",
            "active",
            "absent",
            'odd."key"',
            "a.b",
            "back\\slash",
        ]
        records = {r["id"]: r for r in db.records("styles", fields=wanted)}
        full = {r["id"]: r for r in db.records("styles")}
    two = records["S2"]
    assert two == {key: full["S2"][key] for key in ["id", *wanted] if key in full["S2"]}
    assert "absent" not in two  # missing stays missing, so `in` keeps its meaning
    assert "empty" in two and two["empty"] is None  # an explicit null stays a null
    assert two["active"] is False and two["price"] == 1.5 and two["tags"] == ["a", "b"]
    assert two['odd."key"'] == "quoted" and two["a.b"] == "dotted"
    assert two["back\\slash"] == "bs"
    assert records["S1"] == {"id": "S1", "node_name": "One", "active": True}
    assert list(two) == ["id", *(k for k in wanted if k in two)]  # id first, then as asked


def test_deleted_records_are_left_out_unless_asked(fetched) -> None:
    fake, settings = fetched
    fake.remove("styles", "S3")
    run_fetch(settings, full=True, allow_delete=True, client=fake.client())
    with Reader(settings.database) as db:
        assert [r["id"] for r in db.records("styles")] == ["S1", "S2"]
        assert [r["id"] for r in db.records("styles", include_deleted=True)] == ["S1", "S2", "S3"]
        assert db.count("styles") == 2 and db.count("styles", include_deleted=True) == 3
        assert set(db.records_by_id("styles", fields=["node_name"])) == {"S1", "S2"}


def test_changes_since_a_sequence_number(fetched) -> None:
    fake, settings = fetched
    with Reader(settings.database) as db:
        first = db.change_seq()
        assert first > 0
        assert {c.endpoint: c.total for c in db.changes()} == {"colorways": 1, "styles": 3}

    # Same data again: nothing changes, the counter doesn't move.
    run_fetch(settings, full=True, client=fake.client())
    with Reader(settings.database) as db:
        assert db.change_seq() == first
        assert all(c.total == 0 for c in db.changes(since=first))

    fake.add("styles", id="S1", _modified_at=ts(9), node_name="One again", active=True)
    fake.remove("styles", "S3")
    run_fetch(settings, full=True, allow_delete=True, client=fake.client())
    with Reader(settings.database) as db:
        by = {c.endpoint: c for c in db.changes(since=first, endpoints=["styles", "colorways"])}
        assert (by["styles"].changed, by["styles"].deleted) == (1, 1)
        assert by["colorways"].total == 0
        assert db.change_seq() == by["styles"].last_seq > first


def test_reader_refuses_missing_foreign_and_other_version_databases(tmp_path, fetched) -> None:
    with pytest.raises(DatabaseError, match="not found"):
        Reader(tmp_path / "nope.db")
    foreign = tmp_path / "foreign.db"
    sqlite3.connect(foreign).execute("CREATE TABLE t (x)").connection.commit()
    with pytest.raises(DatabaseError):
        Reader(foreign)
    _fake, settings = fetched
    conn = sqlite3.connect(settings.database)
    conn.execute("UPDATE meta_info SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    with pytest.raises(DatabaseError, match="schema version"):
        Reader(settings.database)


@pytest.mark.parametrize("bad", ["node_name", {"node_name": 1}, [""], [3]])
def test_fields_must_be_a_list_of_names(fetched, bad) -> None:
    _fake, settings = fetched
    with Reader(settings.database) as db, pytest.raises(TypeError):
        list(db.records("styles", fields=bad))


def test_payloads_orjson_rejects_fall_back_to_json(fetched) -> None:
    """orjson is strict: NaN/Infinity and integers beyond 64 bits fall back to json."""
    _fake, settings = fetched
    big = 10**70 + 7
    odd = f'{{"id": "S2", "nan": NaN, "big": {big}, "node_name": "Two"}}'
    conn = sqlite3.connect(settings.database)
    conn.execute("UPDATE bronze_records SET payload = ? WHERE id = 'S2'", [odd])
    conn.commit()
    with Reader(settings.database) as db:
        (record,) = [r for r in db.records("styles", fields=["big", "nan"]) if r["id"] == "S2"]
        (full,) = [r for r in db.records("styles") if r["id"] == "S2"]
    assert record["big"] == big
    assert record["nan"] != record["nan"]  # NaN
    assert full["node_name"] == "Two"


def test_orjson_is_the_default_parser() -> None:
    assert read_module._loads is read_module._loads_orjson


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_reader_explains_an_unwritable_database_directory(fetched) -> None:
    # A reader running as another user than the fetch: it can read the file, but SQLite
    # (WAL) can't create its -shm/-wal files next to it.
    _, settings = fetched
    folder = settings.database.parent
    folder.chmod(0o555)
    try:
        with pytest.raises(DatabaseError, match="needs to create centric.db-shm"):
            Reader(settings.database)
    finally:
        folder.chmod(0o755)
