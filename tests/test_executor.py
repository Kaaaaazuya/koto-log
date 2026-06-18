"""T1.3: ツール executor のテスト（直接呼び出しで DB が変わる）。"""

from datetime import datetime, timezone, timedelta

import pytest

from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.tools.executor import ToolExecutor

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=JST)


@pytest.fixture()
def executor():
    conn = connect(":memory:")
    crud.init_db(conn)
    child_id = crud.ensure_child(conn, "baby")
    yield ToolExecutor(conn=conn, child_id=child_id, now=NOW)
    conn.close()


def test_save_record_inserts_row(executor):
    result = executor.execute(
        "save_record",
        {"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml",
         "started_at": "3時"},
    )
    assert result["ok"] is True
    rec = crud.get_record(executor.conn, result["record"]["id"])
    assert rec["amount"] == 120
    # 「3時」が JST 絶対時刻に正規化されている
    assert rec["started_at"] == "2026-06-18T03:00:00+09:00"


def test_query_records_aggregates(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "3時"})
    executor.execute("save_record", {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "7時"})
    executor.execute("save_record", {"type": "diaper", "started_at": "8時"})

    result = executor.execute("query_records", {"type": "feeding", "period": "today"})
    assert result["count"] == 2
    assert result["total_amount"] == 220


def test_update_last_record(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "3時"})
    result = executor.execute(
        "update_or_delete_record",
        {"target": "last", "action": "update", "new_values": {"amount": 150}},
    )
    assert result["ok"] is True
    rec = crud.get_record(executor.conn, result["record"]["id"])
    assert rec["amount"] == 150


def test_update_normalizes_time_in_new_values(executor):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "started_at": "3時"})
    result = executor.execute(
        "update_or_delete_record",
        {"target": "last", "action": "update", "new_values": {"started_at": "5時"}},
    )
    rec = crud.get_record(executor.conn, result["record"]["id"])
    assert rec["started_at"] == "2026-06-18T05:00:00+09:00"


def test_delete_last_record(executor):
    saved = executor.execute("save_record", {"type": "feeding", "started_at": "3時"})
    rid = saved["record"]["id"]
    result = executor.execute(
        "update_or_delete_record", {"target": "last", "action": "delete"}
    )
    assert result["ok"] is True
    assert crud.get_record(executor.conn, rid) is None


def test_update_or_delete_with_no_record(executor):
    result = executor.execute(
        "update_or_delete_record", {"target": "last", "action": "delete"}
    )
    assert result["ok"] is False


def test_unknown_tool_raises(executor):
    with pytest.raises(ValueError):
        executor.execute("nope", {})
