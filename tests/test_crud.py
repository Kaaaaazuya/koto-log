"""T1.1: DB層（接続・スキーマ・CRUD）のテスト。"""

import pytest

from kotolog.db import crud
from kotolog.db.connection import connect


@pytest.fixture()
def conn():
    c = connect(":memory:")
    crud.init_db(c)
    yield c
    c.close()


def test_init_db_creates_tables(conn):
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"children", "records", "sessions"} <= names


def test_ensure_child_is_idempotent(conn):
    a = crud.ensure_child(conn, "baby")
    b = crud.ensure_child(conn, "baby")
    assert a == b
    rows = conn.execute("SELECT COUNT(*) AS n FROM children").fetchone()
    assert rows["n"] == 1


def test_insert_and_get_record(conn):
    child_id = crud.ensure_child(conn, "baby")
    rid = crud.insert_record(
        conn,
        child_id=child_id,
        type="feeding",
        sub_type="ミルク",
        amount=120,
        unit="ml",
        started_at="2026-06-18T03:00:00+09:00",
    )
    rec = crud.get_record(conn, rid)
    assert rec["type"] == "feeding"
    assert rec["amount"] == 120
    assert rec["created_at"] and rec["updated_at"]


def test_get_last_record(conn):
    child_id = crud.ensure_child(conn, "baby")
    crud.insert_record(
        conn, child_id=child_id, type="feeding", started_at="2026-06-18T01:00:00+09:00"
    )
    last_id = crud.insert_record(
        conn, child_id=child_id, type="diaper", started_at="2026-06-18T05:00:00+09:00"
    )
    last = crud.get_last_record(conn, child_id)
    assert last["id"] == last_id
    assert last["type"] == "diaper"


def test_query_records_by_type_and_period(conn):
    child_id = crud.ensure_child(conn, "baby")
    crud.insert_record(
        conn, child_id=child_id, type="feeding", amount=100, unit="ml",
        started_at="2026-06-18T03:00:00+09:00",
    )
    crud.insert_record(
        conn, child_id=child_id, type="feeding", amount=120, unit="ml",
        started_at="2026-06-18T07:00:00+09:00",
    )
    crud.insert_record(
        conn, child_id=child_id, type="diaper",
        started_at="2026-06-18T08:00:00+09:00",
    )
    # 前日のミルクは範囲外
    crud.insert_record(
        conn, child_id=child_id, type="feeding", amount=999, unit="ml",
        started_at="2026-06-17T07:00:00+09:00",
    )

    rows = crud.query_records(
        conn,
        child_id=child_id,
        type="feeding",
        start="2026-06-18T00:00:00+09:00",
        end="2026-06-18T23:59:59+09:00",
    )
    assert len(rows) == 2
    assert {r["amount"] for r in rows} == {100, 120}


def test_update_record(conn):
    child_id = crud.ensure_child(conn, "baby")
    rid = crud.insert_record(
        conn, child_id=child_id, type="feeding", amount=100, unit="ml",
        started_at="2026-06-18T03:00:00+09:00",
    )
    ok = crud.update_record(conn, rid, {"amount": 150})
    assert ok is True
    assert crud.get_record(conn, rid)["amount"] == 150


def test_delete_record(conn):
    child_id = crud.ensure_child(conn, "baby")
    rid = crud.insert_record(
        conn, child_id=child_id, type="feeding", started_at="2026-06-18T03:00:00+09:00"
    )
    assert crud.delete_record(conn, rid) is True
    assert crud.get_record(conn, rid) is None
    # 二重削除は False
    assert crud.delete_record(conn, rid) is False
