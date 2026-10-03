from __future__ import annotations

import copy
import sqlite3

from centric_api.db import iso, utc_now
from centric_api.fetch import run_fetch
from centric_api.maintenance import run_silver
from tests.fake_centric import FakeCentric, make_settings, swagger, ts


def _db(settings) -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row["name"]: row["type"] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _seed(fake: FakeCentric) -> None:
    fake.add(
        "styles",
        id="S1",
        _modified_at=ts(1),
        node_name="Jacket",
        active=True,
        net_weight=0.5,
        sort_order=3,
        launched_at="2026-01-01T10:00:00.000Z",
        product_colors=["C0/1|Colorway", "C0/2|Colorway"],
        product_sources={"1": "PS2", "0": "PS1", "10": "PS3"},
        x_custom="not in spec",
        _links={"self": "/styles/S1"},
    )
    fake.add("colorways", id="C0/1|Colorway", _modified_at=ts(1), node_name="Black", style="S1")


def test_silver_tables_follow_the_spec(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)

    summary = run_fetch(settings, client=fake.client())

    assert [result.status for result in summary.silver] == ["rebuilt", "rebuilt"]
    conn = _db(settings)
    columns = _columns(conn, "silver_styles")
    assert columns == {
        "id": "TEXT",
        "_row_deleted": "INTEGER",
        "_row_deleted_at": "TEXT",
        "_row_updated_at": "TEXT",
        "_modified_at": "TEXT",
        "node_name": "TEXT",
        "active": "INTEGER",
        "net_weight": "REAL",
        "sort_order": "INTEGER",
        "launched_at": "TEXT",
    }
    row = conn.execute("SELECT * FROM silver_styles WHERE id = 'S1'").fetchone()
    assert (row["active"], row["net_weight"], row["sort_order"]) == (1, 0.5, 3)
    assert row["launched_at"] == "2026-01-01T10:00:00.000Z"
    assert row["_row_deleted"] == 0
    colors = conn.execute(
        'SELECT position, key, value FROM "silver_styles__product_colors" ORDER BY position'
    ).fetchall()
    assert [tuple(r) for r in colors] == [(0, None, "C0/1|Colorway"), (1, None, "C0/2|Colorway")]
    sources = conn.execute(
        'SELECT position, key, value FROM "silver_styles__product_sources" ORDER BY position'
    ).fetchall()
    assert [tuple(r) for r in sources] == [(0, "0", "PS1"), (1, "1", "PS2"), (2, "10", "PS3")]
    assert not any(name.endswith("___links") for name in _tables(conn))
    indexes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")]
    assert "idx_silver_styles__product_colors__value" in indexes
    joined = conn.execute(
        """
        SELECT c.node_name FROM "silver_styles__product_colors" l
        JOIN silver_colorways c ON c.id = l.value WHERE l.id = 'S1'
        """
    ).fetchall()
    assert [r[0] for r in joined] == ["Black"]
    registry = conn.execute(
        "SELECT kind, value_type, spec_type, object_type FROM meta_silver_fields "
        "WHERE endpoint = 'styles' AND field = 'product_colors'"
    ).fetchone()
    assert tuple(registry) == ("list", "text", "array<string>", "Colorway")


def test_unmapped_field_stays_in_bronze_until_spec_describes_it(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)

    summary = run_fetch(settings, client=fake.client())

    assert summary.silver[0].unmapped == ["x_custom"]
    conn = _db(settings)
    assert "x_custom" not in _columns(conn, "silver_styles")
    assert conn.execute("SELECT field FROM meta_unmapped_fields").fetchall()[0][0] == "x_custom"

    spec = copy.deepcopy(fake.specs["v2"])
    spec["definitions"]["Style information"]["properties"]["x_custom"] = {"type": "string"}
    fake.specs["v2"] = spec
    summary = run_fetch(settings, client=fake.client())

    assert summary.silver[0].status == "rebuilt"
    assert summary.silver[0].unmapped == []
    row = conn.execute("SELECT x_custom FROM silver_styles WHERE id = 'S1'").fetchone()
    assert row[0] == "not in spec"
    assert conn.execute("SELECT COUNT(*) FROM meta_unmapped_fields").fetchone()[0] == 0


def test_removed_spec_field_keeps_its_column(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    spec = copy.deepcopy(fake.specs["v2"])
    del spec["definitions"]["Style information"]["properties"]["sort_order"]
    fake.specs["v2"] = spec
    fake.add("styles", id="S2", _modified_at=iso(utc_now()), node_name="New", sort_order=9)

    run_fetch(settings, client=fake.client())

    conn = _db(settings)
    assert _columns(conn, "silver_styles")["sort_order"] == "INTEGER"
    in_spec = conn.execute(
        "SELECT in_spec FROM meta_silver_fields WHERE endpoint='styles' AND field='sort_order'"
    ).fetchone()[0]
    assert in_spec == 0
    assert conn.execute("SELECT sort_order FROM silver_styles WHERE id='S2'").fetchone()[0] == 9


def test_data_contradicting_the_spec_widens_the_column(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.add("styles", id="S2", _modified_at=iso(utc_now()), node_name="Odd", sort_order="12A")
    fake.add("styles", id="S3", _modified_at=iso(utc_now()), node_name="Real", net_weight=2)

    summary = run_fetch(settings, client=fake.client())

    styles = summary.silver[0]
    assert styles.status == "rebuilt"
    assert "widened sort_order to text" in styles.notes
    conn = _db(settings)
    assert _columns(conn, "silver_styles")["sort_order"] == "TEXT"
    rows = dict(conn.execute("SELECT id, sort_order FROM silver_styles").fetchall())
    assert rows == {"S1": "3", "S2": "12A", "S3": None}
    assert conn.execute("SELECT net_weight FROM silver_styles WHERE id='S3'").fetchone()[0] == 2.0

    fake.add("styles", id="S4", _modified_at=iso(utc_now()), node_name="Later", sort_order=5)
    summary = run_fetch(settings, client=fake.client())
    assert summary.silver[0].status == "synced"
    assert conn.execute("SELECT sort_order FROM silver_styles WHERE id='S4'").fetchone()[0] == "5"


def test_integer_column_widens_to_real_for_fractions(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    fake.add("styles", id="S2", _modified_at=ts(2), node_name="Half", sort_order=1.5)
    settings = make_settings(tmp_path)

    summary = run_fetch(settings, client=fake.client())

    assert "widened sort_order to real" in summary.silver[0].notes
    conn = _db(settings)
    assert _columns(conn, "silver_styles")["sort_order"] == "REAL"
    assert dict(conn.execute("SELECT id, sort_order FROM silver_styles").fetchall()) == {
        "S1": 3.0,
        "S2": 1.5,
    }


def test_incremental_sync_rewrites_only_changed_rows_and_links(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    fake.add(
        "styles",
        id="S1",
        _modified_at=iso(utc_now()),
        node_name="Jacket v2",
        product_colors=["C0/3|Colorway"],
    )

    summary = run_fetch(settings, client=fake.client())

    assert (summary.silver[0].status, summary.silver[0].rows) == ("synced", 1)
    conn = _db(settings)
    assert conn.execute("SELECT node_name FROM silver_styles").fetchone()[0] == "Jacket v2"
    colors = conn.execute('SELECT value FROM "silver_styles__product_colors"').fetchall()
    assert [r[0] for r in colors] == ["C0/3|Colorway"]
    assert conn.execute('SELECT COUNT(*) FROM "silver_styles__product_sources"').fetchone()[0] == 0

    summary = run_fetch(settings, client=fake.client())
    assert summary.silver[0].rows == 0


def test_v3_endpoint_falls_back_to_v2_spec(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    styles_v3 = settings.endpoints[0].__class__("styles", "styles", "v3", "count/Style", limit=2)
    settings = settings.__class__(**{**settings.__dict__, "endpoints": (styles_v3,)})

    summary = run_fetch(settings, client=fake.client())

    assert [(item.api_version, item.status) for item in summary.swagger] == [("v3", "failed")]
    assert summary.silver[0].status == "skipped"

    run_fetch(make_settings(tmp_path), client=fake.client(), endpoint_names=["colorways"])
    summary = run_fetch(settings, client=fake.client())
    assert summary.silver[0].status == "rebuilt"
    assert summary.silver[0].notes == ["no v3 spec stored; using the v2 spec"]

    fake.specs["v3"] = swagger(
        {"S": {"type": "object", "properties": {"id": {"type": "string"}, "only_v3": {}}}},
        {"styles": "S"},
    )
    summary = run_fetch(settings, client=fake.client())
    assert summary.silver[0].notes == []
    conn = _db(settings)
    columns = _columns(conn, "silver_styles")
    assert columns["only_v3"] == "TEXT"
    assert "node_name" in columns  # columns are never dropped


def test_silver_rebuild_needs_no_api(tmp_path) -> None:
    fake = FakeCentric()
    _seed(fake)
    settings = make_settings(tmp_path)
    run_fetch(settings, client=fake.client())
    conn = _db(settings)
    conn.execute("DROP TABLE silver_styles")
    conn.commit()

    results = run_silver(settings)

    assert [r.status for r in results] == ["rebuilt", "synced"]
    assert conn.execute("SELECT COUNT(*) FROM silver_styles").fetchone()[0] == 1
    results = run_silver(settings, endpoint_names=["colorways"], rebuild=True)
    assert [(r.endpoint, r.status, r.rows) for r in results] == [("colorways", "rebuilt", 1)]


def test_case_insensitive_field_collision_gets_suffix(tmp_path) -> None:
    fake = FakeCentric()
    spec = copy.deepcopy(fake.specs["v2"])
    spec["definitions"]["Style information"]["properties"]["Node_Name"] = {"type": "string"}
    fake.specs["v2"] = spec
    fake.add("styles", id="S1", _modified_at=ts(1), node_name="a", Node_Name="b")
    settings = make_settings(tmp_path)

    run_fetch(settings, client=fake.client(), endpoint_names=["styles"])

    conn = _db(settings)
    row = conn.execute('SELECT node_name, "Node_Name__2" FROM silver_styles').fetchone()
    assert tuple(row) == ("a", "b")


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
