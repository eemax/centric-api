from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest

from centric_api.db import iso, utc_now
from centric_api.fetch import run_fetch
from tests.fake_centric import FakeCentric, make_settings, ts


def _db(settings) -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database)
    conn.row_factory = sqlite3.Row
    return conn


def _style(fake: FakeCentric, record_id: str, minute: int, **extra) -> None:
    fake.add(
        "styles",
        id=record_id,
        _modified_at=extra.pop("modified_at", ts(minute)),
        node_name=f"Style {record_id}",
        **extra,
    )


def _seed(fake: FakeCentric) -> None:
    for index in range(5):
        _style(fake, f"S{index}", index, active=True, sort_order=index)
    fake.add("colorways", id="C0/1|Colorway", _modified_at=ts(1), node_name="Black", style="S0")


def test_first_fetch_is_full_and_fills_bronze_and_state(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)

    summary = run_fetch(settings, client=fake.client())

    assert summary.status == "ok"
    styles = summary.endpoints[0]
    assert (styles.mode, styles.fetched, styles.expected) == ("full", 5, 5)
    assert styles.counts.inserted == 5
    conn = _db(settings)
    assert conn.execute("SELECT COUNT(*) FROM bronze_records").fetchone()[0] == 6
    payload = json.loads(
        conn.execute("SELECT payload FROM bronze_records WHERE id = 'S3'").fetchone()[0]
    )
    assert payload["node_name"] == "Style S3"
    state = conn.execute("SELECT * FROM meta_endpoint_state WHERE endpoint = 'styles'").fetchone()
    assert state["last_success_mode"] == "full"
    assert state["pending_run_id"] is None
    assert state["last_full_success_at"] is not None
    run = conn.execute("SELECT status, finished_at FROM meta_runs").fetchone()
    assert run["status"] == "ok" and run["finished_at"]


def test_delta_fetch_only_reads_changes_since_last_start(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    conn = _db(settings)
    conn.execute(
        "UPDATE meta_endpoint_state SET last_success_started_at = ?",
        [iso(utc_now() - timedelta(hours=1))],
    )
    conn.commit()
    changed = iso(utc_now() - timedelta(minutes=30))
    _style(fake, "S1", 0, modified_at=changed, active=False)
    _style(fake, "S9", 0, modified_at=changed)

    summary = run_fetch(settings, client=fake.client())

    styles = summary.endpoints[0]
    assert styles.mode == "delta"
    assert styles.delta_floor is not None
    assert (styles.fetched, styles.counts.inserted, styles.counts.updated) == (2, 1, 1)
    assert styles.passes == 1
    conn = _db(settings)
    payload = json.loads(
        conn.execute("SELECT payload FROM bronze_records WHERE id = 'S1'").fetchone()[0]
    )
    assert payload["active"] is False
    page_requests = [
        r for r in fake.requests if r.url.path.endswith("/styles") and r.method == "GET"
    ]
    assert page_requests[-1].url.params["_modified_at"].startswith("ge")


def test_older_payload_never_replaces_newer(tmp_path) -> None:
    fake = FakeCentric()
    _style(fake, "S1", 30)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.add("styles", id="S1", _modified_at=ts(10), node_name="older")

    summary = run_fetch(settings, full=True, client=fake.client(), endpoint_names=["styles"])

    assert summary.endpoints[0].counts.unchanged == 1
    conn = _db(settings)
    row = conn.execute("SELECT modified_at, payload FROM bronze_records WHERE id='S1'").fetchone()
    assert row["modified_at"] == ts(30)
    assert json.loads(row["payload"])["node_name"] == "Style S1"


def test_full_fetch_reports_missing_but_only_deletes_when_allowed(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.remove("styles", "S2")

    summary = run_fetch(settings, full=True, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.status, styles.deleted, styles.missing) == ("ok", 0, 1)
    assert fake.lookups == 1  # missing records are confirmed even without --allow-delete
    conn = _db(settings)
    assert conn.execute("SELECT deleted FROM bronze_records WHERE id='S2'").fetchone()[0] == 0

    summary = run_fetch(
        settings, full=True, allow_delete=True, client=fake.client(), endpoint_names=["styles"]
    )

    styles = summary.endpoints[0]
    assert (styles.deleted, styles.missing) == (1, 0)
    row = conn.execute("SELECT deleted, deleted_at FROM bronze_records WHERE id='S2'").fetchone()
    assert row["deleted"] == 1 and row["deleted_at"]
    silver = conn.execute("SELECT _row_deleted FROM silver_styles WHERE id='S2'").fetchone()
    assert silver["_row_deleted"] == 1
    recorded = conn.execute(
        "SELECT deleted, missing FROM meta_run_endpoints WHERE endpoint='styles' "
        "ORDER BY started_at"
    ).fetchall()
    assert [tuple(r) for r in recorded][-2:] == [(0, 1), (1, 0)]

    _style(fake, "S2", 2)
    summary = run_fetch(settings, full=True, client=fake.client(), endpoint_names=["styles"])

    assert summary.endpoints[0].counts.restored == 1
    assert conn.execute("SELECT deleted FROM bronze_records WHERE id='S2'").fetchone()[0] == 0
    silver = conn.execute("SELECT _row_deleted FROM silver_styles WHERE id='S2'").fetchone()
    assert silver["_row_deleted"] == 0


def test_delete_confirms_each_missing_record_by_id(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())

    def delete_and_insert(endpoint: str, skip: int) -> None:
        if endpoint == "styles" and skip == 2:
            # S0 (already read) is deleted, so S2 shifts into the page already read and is
            # skipped; a new record at the end keeps the total equal to the count.
            fake.remove("styles", "S0")
            _style(fake, "S9", 50)

    fake.page_hook = delete_and_insert
    summary = run_fetch(
        settings, full=True, allow_delete=True, client=fake.client(), endpoint_names=["styles"]
    )

    styles = summary.endpoints[0]
    assert (styles.fetched, styles.expected) == (5, 5)
    assert styles.deleted == 0  # S0 was read before it was deleted; S2 was only skipped
    assert styles.status == "warn"
    assert "still exist in Centric" in styles.warnings[0]
    assert fake.lookups == 1
    conn = _db(settings)
    flags = dict(conn.execute("SELECT id, deleted FROM bronze_records WHERE endpoint='styles'"))
    assert flags == {"S0": 0, "S1": 0, "S2": 0, "S3": 0, "S4": 0, "S9": 0}

    fake.page_hook = None
    summary = run_fetch(
        settings, full=True, allow_delete=True, client=fake.client(), endpoint_names=["styles"]
    )

    assert (summary.endpoints[0].deleted, summary.endpoints[0].status) == (1, "ok")
    assert conn.execute("SELECT deleted FROM bronze_records WHERE id='S0'").fetchone()[0] == 1


def test_lookups_are_batched(tmp_path) -> None:
    fake = FakeCentric()
    for index in range(120):
        _style(fake, f"S{index:03d}", 0, modified_at=f"2026-01-01T01:{index % 60:02d}:00.000Z")
    settings = make_settings(tmp_path, limit=50)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    fake.records["styles"] = fake.records["styles"][:1]

    summary = run_fetch(
        settings, full=True, allow_delete=True, client=fake.client(), endpoint_names=["styles"]
    )

    assert summary.endpoints[0].deleted == 119
    assert fake.lookups == 3


def test_allow_delete_requires_full(tmp_path) -> None:
    from centric_api.config import ConfigError

    with pytest.raises(ConfigError, match="only applies to --full"):
        run_fetch(make_settings(tmp_path), allow_delete=True, client=FakeCentric().client())


def test_delta_fetch_never_flags_deletions(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.remove("styles", "S2")

    summary = run_fetch(settings, client=fake.client())

    assert (summary.endpoints[0].deleted, summary.endpoints[0].missing) == (0, 0)
    conn = _db(settings)
    assert conn.execute("SELECT deleted FROM bronze_records WHERE id='S2'").fetchone()[0] == 0


def test_empty_full_fetch_never_deletes(tmp_path) -> None:
    fake = FakeCentric()
    _style(fake, "S1", 1)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.remove("styles", "S1")

    summary = run_fetch(
        settings, full=True, allow_delete=True, client=fake.client(), endpoint_names=["styles"]
    )

    styles = summary.endpoints[0]
    assert (styles.deleted, styles.status) == (0, "warn")
    assert "returned no records" in styles.warnings[0]
    assert fake.lookups == 0


def test_interrupted_fetch_resumes_where_it_stopped(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    fake.fail_page_after = 1

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.status, styles.fetched) == ("failed", 2)
    assert summary.status == "failed"
    conn = _db(settings)
    state = conn.execute("SELECT * FROM meta_endpoint_state WHERE endpoint='styles'").fetchone()
    assert (state["pending_next_skip"], state["pending_fetched"]) == (2, 2)
    assert state["last_success_started_at"] is None
    pending_run = state["pending_run_id"]

    fake.fail_page_after = None
    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert styles.status == "ok"
    assert styles.resumed is True
    assert (styles.mode, styles.fetched, styles.counts.inserted) == ("full", 5, 3)
    skips = [
        int(r.url.params["skip"])
        for r in fake.requests
        if r.url.path.endswith("/styles") and r.method == "GET"
    ]
    assert skips[-3:] == [2, 4, 5]  # resumed at 2; the empty page at 5 ends the window
    state = conn.execute("SELECT * FROM meta_endpoint_state WHERE endpoint='styles'").fetchone()
    assert state["pending_run_id"] is None
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM bronze_records WHERE last_seen_run = ?", [pending_run]
        ).fetchone()[0]
        == 5
    )


def test_restart_ignores_unfinished_fetch(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    fake.fail_page_after = 1
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    fake.fail_page_after = None

    summary = run_fetch(settings, restart=True, client=fake.client(), endpoint_names=["styles"])

    assert summary.endpoints[0].resumed is False
    assert summary.endpoints[0].fetched == 5


def test_record_moving_during_first_fetch_is_settled_by_a_second_pass(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)

    def touch_first_record(endpoint: str, skip: int) -> None:
        if endpoint == "styles" and skip == 2:
            _style(fake, "S0", 59)  # S0 moves to the end; S2 shifts into the page already read

    fake.page_hook = touch_first_record

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.status, styles.complete, styles.passes) == ("ok", True, 2)
    assert "appeared twice" in styles.notes[0]
    assert "pass 2 found 1 records the earlier passes missed and proved" in styles.notes[1]
    conn = _db(settings)
    ids = {r[0] for r in conn.execute("SELECT id FROM bronze_records WHERE endpoint='styles'")}
    assert ids == {"S0", "S1", "S2", "S3", "S4"}
    state = conn.execute("SELECT * FROM meta_endpoint_state WHERE endpoint='styles'").fetchone()
    assert state["pending_run_id"] is None and state["last_success_started_at"]


def test_count_higher_than_listable_is_proven_by_a_second_pass(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    fake.count_offset = 20  # Centric counts records it never lists

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.status, styles.complete, styles.passes) == ("ok", True, 2)
    assert "fetched 5 of the 25 records Centric counted" in styles.notes[0]
    assert "found nothing the earlier passes missed" in styles.notes[1]

    _style(fake, "S9", 0, modified_at=iso(utc_now()))
    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    styles = summary.endpoints[0]
    assert (styles.mode, styles.status, styles.complete) == ("delta", "ok", True)


def test_delta_that_keeps_changing_holds_the_cursor(tmp_path) -> None:
    fake = FakeCentric()
    for index in range(10):
        _style(fake, f"S{index}", index)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = _db(settings)
    cursor = iso(utc_now() - timedelta(hours=1))
    conn.execute("UPDATE meta_endpoint_state SET last_success_started_at = ?", [cursor])
    conn.commit()
    # Changed inside the window (after the cursor, before this run starts).
    changed = utc_now() - timedelta(minutes=30)
    for index in range(10):
        _style(fake, f"N{index}", 0, modified_at=iso(changed + timedelta(milliseconds=index)))

    inserted = []

    def insert_ahead_of_reader(endpoint: str, skip: int) -> None:
        # Every pass, a record dated inside the window appears before the reading position, so
        # each pass misses a record no earlier pass has seen.
        if endpoint == "styles" and skip == 2:
            inserted.append(f"X{len(inserted)}")
            older = changed - timedelta(minutes=1, seconds=len(inserted))  # sorts first
            _style(fake, inserted[-1], 0, modified_at=iso(older))

    fake.page_hook = insert_ahead_of_reader

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.mode, styles.status, styles.complete, styles.passes) == (
        "delta",
        "warn",
        False,
        3,
    )
    assert "could not be proven complete after 3 passes" in styles.warnings[0]
    assert "cursor was not moved forward" in styles.warnings[1]
    state = conn.execute(
        "SELECT last_success_started_at, pending_run_id FROM meta_endpoint_state "
        "WHERE endpoint='styles'"
    ).fetchone()
    assert (state[0], state[1]) == (cursor, None)

    fake.page_hook = None
    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    assert summary.endpoints[0].delta_floor == styles.delta_floor  # same span read again
    assert (summary.endpoints[0].status, summary.endpoints[0].complete) == ("ok", True)


def test_unproven_later_full_fetch_holds_the_cursor(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = _db(settings)
    cursor = conn.execute("SELECT last_success_started_at FROM meta_endpoint_state").fetchone()[0]
    fake.count_offset = 3

    summary = run_fetch(settings, full=True, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.mode, styles.status, styles.complete, styles.passes) == (
        "full",
        "warn",
        False,
        1,
    )
    assert "could not be proven complete" in styles.warnings[0]
    assert "cursor was not moved forward" in styles.warnings[1]
    state = conn.execute(
        "SELECT last_success_started_at, last_full_success_at FROM meta_endpoint_state"
    ).fetchone()
    assert state[0] == cursor and state[1] is not None


def test_records_created_while_paging_are_not_a_failure(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)

    def create_record(endpoint: str, skip: int) -> None:
        if (
            endpoint == "styles"
            and skip == 2
            and not any(r["id"] == "S9" for r in fake.records[endpoint])
        ):
            _style(fake, "S9", 59)

    fake.page_hook = create_record
    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.status, styles.expected, styles.fetched, styles.passes) == ("ok", 5, 6, 1)


def test_server_page_cap_below_limit_causes_no_gaps(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    fake.page_cap = 1
    summary = run_fetch(
        make_settings(tmp_path, limit=50), client=fake.client(), endpoint_names=["styles"]
    )

    styles = summary.endpoints[0]
    assert (styles.status, styles.fetched, styles.complete) == ("ok", 5, True)


def test_server_ignoring_skip_fails_cleanly(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    fake.ignore_skip = True

    summary = run_fetch(make_settings(tmp_path), client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert styles.status == "failed"
    assert "not honouring 'skip'" in (styles.error or "")


def test_clock_moving_back_forces_a_full_fetch(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = _db(settings)
    conn.execute(
        "UPDATE meta_endpoint_state SET last_success_started_at = '2999-01-01T00:00:00.000Z'"
    )
    conn.commit()

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.mode, styles.status, styles.fetched) == ("full", "warn", 5)
    assert "clock moved back" in styles.warnings[0]


def test_endpoint_without_id_filter_never_flags_deletions(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.remove("styles", "S2")
    fake.ignore_id_filter = True

    summary = run_fetch(
        settings, full=True, allow_delete=True, client=fake.client(), endpoint_names=["styles"]
    )

    styles = summary.endpoints[0]
    assert styles.status == "failed"
    assert "does not support id lookups" in (styles.error or "")
    conn = _db(settings)
    assert conn.execute("SELECT SUM(deleted) FROM bronze_records").fetchone()[0] == 0


def test_failed_endpoint_does_not_stop_others(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    fake.paths["styles"] = "moved"
    settings = make_settings(tmp_path)

    summary = run_fetch(settings, client=fake.client())

    assert [item.status for item in summary.endpoints] == ["failed", "ok"]
    assert summary.status == "partial"


def test_unknown_endpoint_name_is_rejected(tmp_path) -> None:
    from centric_api.config import ConfigError

    with pytest.raises(ConfigError, match="Unknown endpoint"):
        run_fetch(make_settings(tmp_path), endpoint_names=["nope"], client=FakeCentric().client())


def test_missing_endpoint_gets_a_clear_message(tmp_path) -> None:
    fake = FakeCentric()
    fake.count_paths = {"count/Colorway": "colorways"}
    summary = run_fetch(make_settings(tmp_path), client=fake.client())

    styles, colorways = summary.endpoints
    assert "does not exist on this Centric instance" in (styles.error or "")
    assert colorways.status == "ok"


def test_bronze_keeps_centric_key_order(tmp_path) -> None:
    fake = FakeCentric()
    fake.add("styles", id="S1", _modified_at=ts(1), zeta=1, alpha=2)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    fake.records["styles"] = [{"alpha": 2, "zeta": 1, "_modified_at": ts(1), "id": "S1"}]

    summary = run_fetch(settings, full=True, client=fake.client(), endpoint_names=["styles"])

    assert summary.endpoints[0].counts.unchanged == 1  # same content, different key order
    conn = _db(settings)
    payload = conn.execute("SELECT payload FROM bronze_records").fetchone()[0]
    assert list(json.loads(payload)) == ["id", "_modified_at", "zeta", "alpha"]
    command = conn.execute("SELECT command FROM meta_runs ORDER BY started_at").fetchall()
    assert [r[0] for r in command] == ["fetch -e styles", "fetch --full -e styles"]


def test_delete_and_create_during_paging_cannot_hide_a_skipped_record(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = _db(settings)
    conn.execute(
        "UPDATE meta_endpoint_state SET last_success_started_at = ?",
        [iso(utc_now() - timedelta(hours=1))],
    )
    conn.commit()
    changed = utc_now() - timedelta(minutes=30)
    for index in range(6):
        _style(fake, f"N{index}", 0, modified_at=iso(changed + timedelta(milliseconds=index)))

    def delete_and_create(endpoint: str, skip: int) -> None:
        if (
            endpoint == "styles"
            and skip == 2
            and not any(r["id"] == "Z" for r in fake.records[endpoint])
        ):
            fake.remove("styles", "N0")  # already read: N2 shifts into the page already read
            _style(fake, "Z", 0, modified_at=iso(utc_now() + timedelta(seconds=1)))

    fake.page_hook = delete_and_create

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.expected, styles.status, styles.complete, styles.passes) == (6, "ok", True, 2)
    assert "changed while the window was being read" in styles.notes[0]
    assert "pass 2 found 1 records the earlier passes missed" in styles.notes[1]
    ids = {r[0] for r in conn.execute("SELECT id FROM bronze_records WHERE id LIKE 'N%'")}
    assert ids == {"N0", "N1", "N2", "N3", "N4", "N5"}


def test_quiet_delta_needs_one_pass(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path, overlap_minutes=10)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = _db(settings)
    conn.execute(
        "UPDATE meta_endpoint_state SET last_success_started_at = ?",
        [iso(utc_now() - timedelta(hours=2))],
    )
    conn.commit()
    _style(fake, "N1", 0, modified_at=iso(utc_now() - timedelta(hours=1)))

    summary = run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.fetched, styles.passes, styles.complete, styles.notes) == (1, 1, True, [])


def test_later_full_fetch_with_changes_keeps_cursor_without_warning(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])
    conn = _db(settings)
    cursor = conn.execute("SELECT last_success_started_at FROM meta_endpoint_state").fetchone()[0]

    def create_record(endpoint: str, skip: int) -> None:
        if (
            endpoint == "styles"
            and skip == 2
            and not any(r["id"] == "Z" for r in fake.records[endpoint])
        ):
            _style(fake, "Z", 0, modified_at=iso(utc_now() + timedelta(seconds=1)))

    fake.page_hook = create_record
    summary = run_fetch(settings, full=True, client=fake.client(), endpoint_names=["styles"])

    styles = summary.endpoints[0]
    assert (styles.status, styles.complete, styles.passes, styles.warnings) == ("ok", False, 1, [])
    assert "cursor stays at its previous position" in styles.notes[0]
    assert conn.execute("SELECT last_success_started_at FROM meta_endpoint_state").fetchone()[
        0
    ] == (cursor)
